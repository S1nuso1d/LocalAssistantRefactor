"""Optional local Windows text-to-speech with an interruptible child process."""
from __future__ import annotations

import base64
import os
import re
import subprocess
import threading


def plain_text_for_speech(markdown: str, limit: int = 12000) -> str:
    """Remove code blocks and common Markdown syntax before speaking."""
    text = re.sub(r"\x60{3}.*?\x60{3}", " Блок кода пропущен. ", markdown or "", flags=re.DOTALL)
    text = re.sub(r"!?[([^]]+)]([^)]+)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    text = re.sub(r"[*_\x60~]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


class SpeechOutput:
    """Use Windows' built-in SAPI synthesizer; killing the process interrupts speech."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None

    def speak(self, markdown: str) -> bool:
        text = plain_text_for_speech(markdown)
        if not text or os.name != "nt":
            return False
        self.stop()
        text_b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
        script = (
            "$t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
            + text_b64
            + "')); Add-Type -AssemblyName System.Speech; "
              "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$s.Rate=0; $s.Speak($t); $s.Dispose()"
        )
        encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        try:
            process = subprocess.Popen(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_script],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            return False
        with self._lock:
            self._process = process
        return True

    def is_speaking(self) -> bool:
        with self._lock:
            if self._process is None:
                return False
            if self._process.poll() is not None:
                self._process = None
                return False
            return True

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=0.4)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=0.4)
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            pass
