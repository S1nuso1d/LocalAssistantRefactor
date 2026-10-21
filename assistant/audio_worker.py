"""System-loopback capture and Whisper transcription in a dedicated thread."""
from __future__ import annotations

import logging
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
    SAMPLE_RATE,
    SILENCE_DURATION,
    SILENCE_THRESHOLD,
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_MODEL,
)
from .settings_store import SettingsStore

logger = logging.getLogger("live_assistant.audio")


class AudioWorker(QThread):
    phrase_detected = Signal(str)
    status_changed = Signal(str)
    listening_changed = Signal(bool)
    audio_level = Signal(float)

    def __init__(self, settings_store: SettingsStore | None = None) -> None:
        super().__init__()
        self.settings = settings_store or SettingsStore()
        self._running = True
        self._listening = True
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
            return " ".join(s.text.strip() for s in segments if s.text.strip()).strip()
        except Exception:
            logger.exception("Ошибка распознавания аудио.")
            self.status_changed.emit("Ошибка распознавания речи")
            return ""

    def run(self) -> None:
        try:
            self.status_changed.emit("Загружаю модель распознавания…")
            model_name = str(self.settings.get("whisper_model", WHISPER_MODEL))
            self._whisper = WhisperModel(
                model_name,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
            )
            mic = self._loopback_microphone()
            self.status_changed.emit("Слушаю системный звук")

            buffer: list[np.ndarray] = []
            speech_seconds = 0.0
            silence_seconds = 0.0
            in_speech = False

            with mic.recorder(samplerate=SAMPLE_RATE) as recorder:
                while self._running:
                    try:
                        if not self._listening:
                            buffer.clear()
                            speech_seconds = silence_seconds = 0.0
                            in_speech = False
                            self.audio_level.emit(0.0)
                            time.sleep(0.05)
                            continue

                        audio = recorder.record(numframes=CHUNK_FRAMES)
                        if audio is None or len(audio) == 0:
                            continue
                        audio = np.asarray(audio, dtype=np.float32)
                        if audio.ndim > 1:
                            audio = np.mean(audio, axis=1)
                        rms = float(np.sqrt(np.mean(audio ** 2) + 1e-12))
                        self.audio_level.emit(min(1.0, rms * 12.0))

                        threshold = float(self.settings.get("silence_threshold", SILENCE_THRESHOLD))
                        silence_limit = float(self.settings.get("silence_duration", SILENCE_DURATION))

                        if rms >= threshold:
                            buffer.append(audio.copy())
                            speech_seconds += CHUNK_DURATION
                            silence_seconds = 0.0
                            in_speech = True
                        elif in_speech:
                            buffer.append(audio.copy())
                            silence_seconds += CHUNK_DURATION

                        finished = in_speech and silence_seconds >= silence_limit
                        too_long = speech_seconds >= MAX_PHRASE_DURATION
                        if finished or too_long:
                            if buffer and speech_seconds >= MIN_SPEECH_DURATION:
                                self.status_changed.emit("Распознаю речь…")
                                transcript = self._transcribe(np.concatenate(buffer))
                                if transcript:
                                    logger.info("Распознано: %s", transcript)
                                    self.phrase_detected.emit(transcript)
                            buffer.clear()
                            speech_seconds = silence_seconds = 0.0
                            in_speech = False
                            self.audio_level.emit(0.0)
                            if self._running and self._listening:
                                self.status_changed.emit("Слушаю системный звук")
                    except Exception:
                        logger.exception("Ошибка захвата системного аудио; повторяю.")
                        self.status_changed.emit("Проблема с аудио — пытаюсь восстановить запись")
                        time.sleep(0.5)
        except Exception as exc:
            logger.exception("Аудиопоток не запущен.")
            self.status_changed.emit(f"Ошибка аудио: {exc}")
