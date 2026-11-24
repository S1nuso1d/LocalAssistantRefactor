"""Optional offline speech output through the built-in Windows SAPI voices.

No extra Python dependency: text is piped to a short-lived PowerShell process
that uses System.Speech. The module is Qt-free so it can be unit-tested.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
from typing import Callable

from .text_utils import strip_thinking

logger = logging.getLogger("live_assistant.tts")

MAX_SPOKEN_CHARS = 1800
_CREATE_NO_WINDOW = 0x08000000

_CODE_FENCE = re.compile(r"(```|~~~).*?(\1|\Z)", re.DOTALL)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF"
    "\U0000FE0F\U0000200D]+"
)
_LINE_MARKERS = re.compile(r"^\s*(#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)", re.MULTILINE)
_TABLE_RULE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$", re.MULTILINE)


def clean_for_speech(markdown: str, limit: int = MAX_SPOKEN_CHARS) -> str:
    """Turn a Markdown answer into text that sounds natural when read aloud."""
    text = strip_thinking(markdown or "")
    text = _CODE_FENCE.sub(" Пример кода показан на экране. ", text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("ссылка", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _TABLE_RULE.sub("", text)
    text = text.replace("|", ", ")
    text = _LINE_MARKERS.sub("", text)
    text = re.sub(r"(\*\*|__|\*|_|~~)", "", text)
    text = _EMOJI.sub("", text)

    # Newlines become sentence pauses unless punctuation is already there.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = ""
    for line in lines:
        if joined and joined[-1] not in ".!?…:;":
            joined += "."
        joined += (" " if joined else "") + line
    joined = re.sub(r"\s+", " ", joined).strip()

    if len(joined) > limit:
        cut = joined[:limit]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        joined = (cut[: end + 1] if end > limit // 2 else cut).strip()
    return joined


def build_powershell_script(
    rate: int, volume: int, voice: str, culture_prefix: str
) -> str:
    """PowerShell that reads UTF-8 text from stdin and speaks it."""
    rate = max(-10, min(10, int(rate)))
    volume = max(0, min(100, int(volume)))
    parts = [
        "[Console]::InputEncoding=[Text.Encoding]::UTF8",
        "Add-Type -AssemblyName System.Speech",
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer",
        f"$s.Rate={rate}",
        f"$s.Volume={volume}",
    ]
    voice = voice.strip()
    if voice:
        escaped = voice.replace("'", "''")
        parts.append(f"try {{ $s.SelectVoice('{escaped}') }} catch {{}}")
    elif re.fullmatch(r"[a-z]{2}", culture_prefix or ""):
        parts.append(
            "$v=$s.GetInstalledVoices() | Where-Object "
            f"{{ $_.VoiceInfo.Culture.Name -like '{culture_prefix}*' }} "
            "| Select-Object -First 1"
        )
        parts.append("if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }")
    parts.append("$t=[Console]::In.ReadToEnd()")
    parts.append("$s.Speak($t)")
    return "; ".join(parts)


class SpeechEngine:
    """Speaks one utterance at a time; a new one replaces the current."""

    def __init__(
        self,
        settings,
        on_state: Callable[[bool], None] | None = None,
        popen=subprocess.Popen,
        platform: str = sys.platform,
    ) -> None:
        self.settings = settings
        self._on_state = on_state
        self._popen = popen
        self._platform = platform
        self._lock = threading.RLock()
        self._generation = 0
        self._proc = None
        self._speaking = False

    @property
    def available(self) -> bool:
        return self._platform == "win32"

    @property
    def speaking(self) -> bool:
        with self._lock:
            return self._speaking

    def _set_state_locked(self, value: bool) -> None:
        """Change and publish speaking state while holding ``_lock``.

        The callback used by the UI only emits a Qt signal. Keeping the state
        transition and notification in one critical section prevents Stop
        from being overtaken by a late "speaking=True" notification.
        """
        if self._speaking == value:
            return
        self._speaking = value
        if self._on_state is not None:
            try:
                self._on_state(value)
            except Exception:
                logger.exception("Ошибка обработчика состояния озвучки.")

    def build_command(self) -> list[str]:
        language = str(self.settings.get("language", "ru"))
        script = build_powershell_script(
            int(self.settings.get("tts_rate", 0)),
            int(self.settings.get("tts_volume", 100)),
            str(self.settings.get("tts_voice", "")),
            "" if language == "auto" else language,
        )
        return [
            "powershell.exe", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-Command", script,
        ]

    def speak(self, text: str) -> bool:
        if not self.available:
            return False
        cleaned = clean_for_speech(text)
        if not cleaned:
            return False
        generation = self._interrupt(emit=False)
        threading.Thread(
            target=self._run, args=(cleaned, generation),
            name="tts", daemon=True,
        ).start()
        return True

    def stop(self) -> None:
        self._interrupt(emit=True)

    def _interrupt(self, emit: bool) -> int:
        """Invalidate older speech workers and return this operation's token."""
        with self._lock:
            self._generation += 1
            generation = self._generation
            proc, self._proc = self._proc, None
            if emit:
                self._set_state_locked(False)
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                logger.debug("Не удалось принудительно завершить процесс озвучки.", exc_info=True)
        return generation

    def _run(self, text: str, generation: int) -> None:
        flags = _CREATE_NO_WINDOW if self._platform == "win32" else 0
        try:
            proc = self._popen(
                self.build_command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        except OSError:
            logger.exception("Не удалось запустить PowerShell для озвучки.")
            with self._lock:
                if generation == self._generation:
                    self._set_state_locked(False)
            return

        with self._lock:
            if generation != self._generation:
                rejected = True
            else:
                rejected = False
                self._proc = proc
                self._set_state_locked(True)
        if rejected:
            try:
                proc.kill()
            except OSError:
                pass
            return

        try:
            proc.communicate(text.encode("utf-8"), timeout=300)
        except Exception:
            logger.exception("Озвучка прервана.")
            try:
                proc.kill()
            except OSError:
                pass
        finally:
            with self._lock:
                if generation == self._generation:
                    self._proc = None
                    self._set_state_locked(False)
