"""System-loopback capture and Whisper transcription in a dedicated thread."""
from __future__ import annotations

import logging
import queue
import threading
import time

import numpy as np
import soundcard as sc
from faster_whisper import WhisperModel
from PySide6.QtCore import QThread, Signal

from .config import (
    CHUNK_DURATION,
    CHUNK_FRAMES,
    LANGUAGE,
    MAX_PHRASE_DURATION,
    MIN_SPEECH_DURATION,
    PREROLL_CHUNKS,
    SAMPLE_RATE,
    SILENCE_DURATION,
    SILENCE_THRESHOLD,
    WHISPER_DEVICE,
    WHISPER_MODEL,
)
from .privacy import describe_text
from .segmenter import PhraseSegmenter, put_drop_oldest, segment_is_reliable
from .settings_store import SettingsStore

logger = logging.getLogger("live_assistant.audio")

TTS_TAIL_SECONDS = 0.8  # room for reverb/buffering after speech ends


class AudioWorker(QThread):
    phrase_detected = Signal(str)
    status_changed = Signal(str)
    listening_changed = Signal(bool)
    audio_level = Signal(float)
    segment_dropped = Signal(str)

    def __init__(self, settings_store: SettingsStore | None = None) -> None:
        super().__init__()
        self.settings = settings_store or SettingsStore()
        self._running = True
        self._listening = True
        self._speaking = False
        self._mute_until = 0.0
        self._whisper = None

    def stop(self) -> None:
        self._running = False

    def set_listening(self, enabled: bool) -> None:
        self._listening = bool(enabled)
        self.listening_changed.emit(self._listening)
        self.status_changed.emit(
            "Слушаю системный звук" if self._listening else "Прослушивание на паузе"
        )

    @property
    def listening(self) -> bool:
        return self._listening

    def set_speaking(self, speaking: bool) -> None:
        """Mute capture while our own voice plays through the loopback."""
        self._speaking = bool(speaking)
        if not speaking:
            self._mute_until = time.monotonic() + TTS_TAIL_SECONDS

    def _log_text(self) -> bool:
        return bool(self.settings.get("log_transcripts", False))

    def _is_muted(self) -> bool:
        return self._speaking or time.monotonic() < self._mute_until

    def _loopback_microphone(self):
        selected = str(self.settings.get("audio_device", "По умолчанию"))
        try:
            speakers = sc.all_speakers()
        except Exception:
            speakers = []

        speaker = None
        if selected and selected != "По умолчанию":
            speaker = next((s for s in speakers if s.name == selected), None)
            if speaker is None:
                self.status_changed.emit(
                    f"Устройство «{selected}» не найдено; использую устройство по умолчанию"
                )

        if speaker is None:
            speaker = sc.default_speaker()
        if speaker is None:
            raise RuntimeError("Windows не сообщает устройство вывода по умолчанию.")

        logger.info("Устройство вывода: %s", speaker.name)
        try:
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            logger.info("Loopback-устройство: %s", mic.name)
            return mic
        except Exception:
            logger.exception("Не удалось получить loopback по имени динамика; ищу среди устройств.")

        for mic in sc.all_microphones(include_loopback=True):
            name = getattr(mic, "name", "")
            if speaker.name.lower() in name.lower():
                logger.info("Loopback-устройство найдено: %s", name)
                return mic
        raise RuntimeError(
            "Не найден loopback-микрофон системного звука. Проверь устройство вывода Windows "
            "и доступность loopback в SoundCard."
        )

    def _transcribe(self, audio: np.ndarray) -> str:
        try:
            audio = np.nan_to_num(np.asarray(audio, dtype=np.float32))
            language = str(self.settings.get("language", LANGUAGE))
            segments, _ = self._whisper.transcribe(
                audio,
                language=None if language == "auto" else language,
                beam_size=3,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            parts = []
            for segment in segments:
                text = segment.text.strip()
                if not text:
                    continue
                if not segment_is_reliable(
                    getattr(segment, "no_speech_prob", None),
                    getattr(segment, "avg_logprob", None),
                    getattr(segment, "compression_ratio", None),
                ):
                    logger.info(
                        "Ненадёжный сегмент отброшен: %s",
                        describe_text(text, self._log_text()),
                    )
                    self.segment_dropped.emit(text)
                    continue
                parts.append(text)
            return " ".join(parts).strip()
        except Exception:
            logger.exception("Ошибка распознавания аудио.")
            self.status_changed.emit("Ошибка распознавания речи")
            return ""

    def _load_whisper(self) -> None:
        model_name = str(self.settings.get("whisper_model", WHISPER_MODEL))
        device = str(self.settings.get("whisper_device", WHISPER_DEVICE))
        compute = {"cpu": "int8", "cuda": "float16"}.get(device, "auto")
        try:
            self._whisper = WhisperModel(
                model_name, device=device, compute_type=compute
            )
        except Exception:
            if device == "cpu":
                raise
            logger.exception("Whisper не запустился на %s; перехожу на CPU.", device)
            self.status_changed.emit(f"Whisper: {device} недоступен, использую CPU")
            self._whisper = WhisperModel(
                model_name, device="cpu", compute_type="int8"
            )

    def _transcribe_loop(self, phrases: "queue.Queue", stop: threading.Event) -> None:
        """Recognise phrases off the capture thread so audio is never missed."""
        while self._running and not stop.is_set():
            try:
                phrase = phrases.get(timeout=0.2)
            except queue.Empty:
                continue
            if not self._listening:
                continue  # paused while this phrase was waiting
            self.status_changed.emit("Распознаю речь…")
            transcript = self._transcribe(phrase)
            if transcript and self._listening:
                logger.info(
                    "Распознано: %s", describe_text(transcript, self._log_text())
                )
                self.phrase_detected.emit(transcript)
            if self._running and self._listening and phrases.empty():
                self.status_changed.emit("Слушаю системный звук")

    def run(self) -> None:
        transcriber: threading.Thread | None = None
        transcriber_stop = threading.Event()
        try:
            self.status_changed.emit("Загружаю модель распознавания…")
            self._load_whisper()
            phrases: queue.Queue = queue.Queue(maxsize=3)
            transcriber = threading.Thread(
                target=self._transcribe_loop,
                args=(phrases, transcriber_stop),
                name="whisper-transcriber",
                daemon=True,
            )
            transcriber.start()
            mic = self._loopback_microphone()
            self.status_changed.emit("Слушаю системный звук")

            segmenter = PhraseSegmenter(
                CHUNK_DURATION, MIN_SPEECH_DURATION,
                MAX_PHRASE_DURATION, PREROLL_CHUNKS,
            )

            with mic.recorder(samplerate=SAMPLE_RATE) as recorder:
                while self._running:
                    try:
                        audio = recorder.record(numframes=CHUNK_FRAMES)
                        if not self._listening or self._is_muted():
                            # Keep draining the device while paused; otherwise
                            # the driver buffer fills with stale audio that
                            # floods the pipeline on resume.
                            segmenter.reset(clear_preroll=True)
                            self.audio_level.emit(0.0)
                            continue
                        if audio is None or len(audio) == 0:
                            continue
                        audio = np.asarray(audio, dtype=np.float32)
                        if audio.ndim > 1:
                            audio = np.mean(audio, axis=1)
                        rms = float(np.sqrt(np.mean(audio ** 2) + 1e-12))
                        self.audio_level.emit(min(1.0, rms * 12.0))

                        threshold = float(self.settings.get("silence_threshold", SILENCE_THRESHOLD))
                        silence_limit = float(self.settings.get("silence_duration", SILENCE_DURATION))

                        phrase = segmenter.feed(audio, rms, threshold, silence_limit)
                        if phrase is not None:
                            if put_drop_oldest(phrases, phrase):
                                logger.warning("Распознавание не успевает: старая фраза отброшена.")
                    except Exception:
                        logger.exception("Ошибка захвата системного аудио; повторяю.")
                        self.status_changed.emit("Проблема с аудио — пытаюсь восстановить запись")
                        time.sleep(0.5)
        except Exception as exc:
            logger.exception("Аудиопоток не запущен.")
            self.status_changed.emit(f"Ошибка аудио: {exc}")
        finally:
            transcriber_stop.set()
            if transcriber is not None:
                transcriber.join(timeout=15)
