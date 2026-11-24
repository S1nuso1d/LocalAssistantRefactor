"""Concurrent microphone/loopback capture with one serialized Whisper worker."""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

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
from .audio_sources import (
    DEFAULT_DEVICE_LABEL,
    device_identity,
    device_name,
    resolve_device,
)
from .devicewatch import DeviceWatcher, RetryBackoff
from .privacy import describe_text
from .segmenter import (
    AdaptiveNoiseGate,
    PhraseSegmenter,
    put_drop_oldest,
    speech_activity_ratio,
    segment_is_reliable,
)
from .settings_store import SettingsStore
from .thread_lifecycle import join_thread_with_warning

logger = logging.getLogger("live_assistant.audio")

TTS_TAIL_SECONDS = 0.8
DEVICE_CHECK_SECONDS = 4.0
RETRY_INITIAL_SECONDS = 1.0
RETRY_MAX_SECONDS = 15.0
MAX_PENDING_PHRASE_AGE_SECONDS = 30.0
SHUTDOWN_JOIN_POLL_SECONDS = 0.25
SHUTDOWN_WARNING_AFTER_SECONDS = 5.0
SOURCE_SYSTEM = "system"
SOURCE_MICROPHONE = "microphone"
SOURCE_LABELS = {
    SOURCE_SYSTEM: "системный звук",
    SOURCE_MICROPHONE: "микрофон",
}


class AudioWorker(QThread):
    """Capture enabled sources concurrently and transcribe phrases serially.

    Each source owns its recorder, segmenter, noise estimator, retry policy and
    bounded phrase queue. Whisper is shared by one dedicated transcriber thread
    because running multiple concurrent inference calls on one model instance
    is not a supported concurrency contract.
    """

    # Kept for compatibility with integrations that only need the transcript.
    phrase_detected = Signal(str)
    # New source-aware signal: (transcript, source_id).
    phrase_detected_with_source = Signal(str, str)
    status_changed = Signal(str)
    source_status_changed = Signal(str, str)
    listening_changed = Signal(bool)
    audio_level = Signal(float)
    source_audio_level = Signal(str, float)
    segment_dropped = Signal(str)
    shutdown_delayed = Signal(str)

    def __init__(self, settings_store: SettingsStore | None = None) -> None:
        super().__init__()
        self.settings = settings_store or SettingsStore()
        self._running = True
        self._listening = True
        self._speaking = False
        self._mute_until = 0.0
        self._whisper = None

        self._generation_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._stream_generations = {SOURCE_SYSTEM: 0, SOURCE_MICROPHONE: 0}
        self._source_queues: dict[str, queue.Queue] = {}
        self._phrases_queue: queue.Queue | None = None  # legacy system queue alias
        self._capture_threads: dict[str, threading.Thread] = {}
        self._source_stop_events: dict[str, threading.Event] = {}
        self._phrase_wakeup = threading.Event()
        self._transcriber_stop_event = threading.Event()
        self._source_cancel_events = {
            SOURCE_SYSTEM: threading.Event(),
            SOURCE_MICROPHONE: threading.Event(),
        }
        self._level_lock = threading.Lock()
        self._source_levels = {SOURCE_SYSTEM: 0.0, SOURCE_MICROPHONE: 0.0}

    def stop(self) -> None:
        self._running = False
        self._transcriber_stop_event.set()
        with self._lifecycle_lock:
            source_stops = list(self._source_stop_events.values())
        for event in source_stops:
            event.set()
        with self._generation_lock:
            for event in self._source_cancel_events.values():
                event.set()
        self._phrase_wakeup.set()

    def set_listening(self, enabled: bool) -> None:
        self._listening = bool(enabled)
        if not self._listening:
            self._invalidate_pending_phrases()
        self.listening_changed.emit(self._listening)
        self.status_changed.emit(
            "Прослушивание активно" if self._listening else "Прослушивание на паузе"
        )

    @property
    def listening(self) -> bool:
        return self._listening

    def set_speaking(self, speaking: bool) -> None:
        """Mute loopback and, by default, mic input while TTS plays."""
        self._speaking = bool(speaking)
        if not speaking:
            self._mute_until = time.monotonic() + TTS_TAIL_SECONDS
        # Microphone passthrough during TTS is an explicit user opt-in. Without
        # acoustic echo cancellation, loudspeaker playback can feed back into it.

    def _is_muted(self, source_id: str) -> bool:
        during_tts = self._speaking or time.monotonic() < self._mute_until
        if source_id == SOURCE_MICROPHONE and bool(
            self.settings.get("microphone_during_tts", False)
        ):
            return False
        return during_tts

    def _log_text(self) -> bool:
        return bool(self.settings.get("log_transcripts", False))

    def _selection_values(self, source_id: str) -> tuple[str, str]:
        if source_id == SOURCE_SYSTEM:
            return (
                str(self.settings.get("audio_device_id", "") or ""),
                str(self.settings.get("audio_device", DEFAULT_DEVICE_LABEL)),
            )
        return (
            str(self.settings.get("microphone_device_id", "") or ""),
            str(self.settings.get("microphone_device", DEFAULT_DEVICE_LABEL)),
        )

    @staticmethod
    def _device_list(source_id: str) -> list[Any]:
        if source_id == SOURCE_SYSTEM:
            return list(sc.all_speakers())
        return list(sc.all_microphones(include_loopback=False))

    @staticmethod
    def _default_device(source_id: str) -> Any:
        return (
            sc.default_speaker()
            if source_id == SOURCE_SYSTEM
            else sc.default_microphone()
        )

    def _resolve_source(self, source_id: str) -> tuple[Any, str, str, bool, str, bool]:
        devices = self._device_list(source_id)
        default_device = self._default_device(source_id)
        selected_id, selected_name = self._selection_values(source_id)
        selection = resolve_device(
            devices, default_device, selected_id, selected_name
        )
        device = selection.device
        identity = device_identity(device)
        name = device_name(device)

        if source_id == SOURCE_SYSTEM:
            # On WASAPI the speaker endpoint ID also identifies its loopback
            # microphone. Fall back to the display name for older SoundCard
            # backends whose IDs are not accepted by get_microphone().
            try:
                capture_device = sc.get_microphone(
                    id=getattr(device, "id", name), include_loopback=True
                )
            except Exception:
                capture_device = sc.get_microphone(id=name, include_loopback=True)
        else:
            capture_device = device

        requested_token = selected_id or (
            selected_name if selected_name != DEFAULT_DEVICE_LABEL else ""
        )
        return (
            capture_device,
            identity,
            name,
            selection.follows_default,
            requested_token,
            selection.requested_available,
        )

    def _current_source_state(
        self, source_id: str
    ) -> tuple[str | None, list[str] | None, str]:
        selected_id, selected_name = self._selection_values(source_id)
        selected_token = selected_id or (
            selected_name if selected_name != DEFAULT_DEVICE_LABEL else ""
        )
        try:
            devices = self._device_list(source_id)
            available = [device_identity(item) for item in devices]
            # Keep names in the availability set for installations migrated
            # from old settings that stored display names rather than IDs.
            available.extend(device_name(item).casefold() for item in devices)
            default_device = self._default_device(source_id)
            default_token = device_identity(default_device) if default_device else None
            return default_token, available, selected_token
        except Exception:
            logger.debug("Не удалось обновить список устройств %s", source_id, exc_info=True)
            return None, None, selected_token

    # Compatibility helpers retained for tests and older internal call sites.
    def _loopback_microphone(self):
        capture, identity, name, follows, selected, available = self._resolve_source(
            SOURCE_SYSTEM
        )
        return capture, name, follows, selected or DEFAULT_DEVICE_LABEL, available

    @staticmethod
    def _current_default_name() -> str | None:
        try:
            device = sc.default_speaker()
            return device_identity(device) if device is not None else None
        except Exception:
            return None

    @staticmethod
    def _current_speaker_names() -> list[str] | None:
        try:
            devices = sc.all_speakers()
            result: list[str] = []
            for item in devices:
                result.extend([device_identity(item), device_name(item).casefold()])
            return result
        except Exception:
            logger.debug("Не удалось получить список устройств вывода.", exc_info=True)
            return None

    @staticmethod
    def _drain_phrase_queue(phrases: queue.Queue) -> int:
        discarded = 0
        while True:
            try:
                phrases.get_nowait()
                discarded += 1
            except queue.Empty:
                return discarded

    def _current_stream_generation(self, source_id: str = SOURCE_SYSTEM) -> int:
        with self._generation_lock:
            return int(self._stream_generations.get(source_id, 0))

    def _invalidate_pending_phrases(
        self,
        phrases: queue.Queue | None = None,
        source_id: str | None = None,
    ) -> None:
        """Invalidate stale queued/in-flight transcripts for one or all sources."""
        sources = [source_id] if source_id else [SOURCE_SYSTEM, SOURCE_MICROPHONE]
        with self._generation_lock:
            for item in sources:
                self._stream_generations[item] = self._stream_generations.get(item, 0) + 1
                current_cancel = self._source_cancel_events.get(item)
                if current_cancel is not None:
                    current_cancel.set()
                self._source_cancel_events[item] = threading.Event()
        queues = [phrases] if phrases is not None else [
            self._source_queues.get(item) for item in sources
        ]
        for target in queues:
            if target is not None:
                discarded = self._drain_phrase_queue(target)
                if discarded:
                    logger.info("Отброшено %d устаревших фраз из источника %s", discarded, source_id or "all")
        self._phrase_wakeup.set()

    def _sleep(self, seconds: float, stop: threading.Event | None = None) -> None:
        end = time.monotonic() + seconds
        while self._running and (stop is None or not stop.is_set()) and time.monotonic() < end:
            time.sleep(0.1)

    def _load_whisper(self) -> None:
        model_name = str(self.settings.get("whisper_model", WHISPER_MODEL))
        device = str(self.settings.get("whisper_device", WHISPER_DEVICE))
        compute = {"cpu": "int8", "cuda": "float16"}.get(device, "auto")
        try:
            self._whisper = WhisperModel(model_name, device=device, compute_type=compute)
        except Exception:
            if device == "cpu":
                raise
            logger.exception("Whisper не запустился на %s; перехожу на CPU.", device)
            self.status_changed.emit(f"Whisper: {device} недоступен, использую CPU")
            self._whisper = WhisperModel(model_name, device="cpu", compute_type="int8")

    def _transcribe(
        self, audio: np.ndarray, stop_event: threading.Event | None = None
    ) -> str:
        try:
            if stop_event is not None and stop_event.is_set():
                return ""
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
                if stop_event is not None and stop_event.is_set():
                    return ""
                text = segment.text.strip()
                if not text:
                    continue
                if not segment_is_reliable(
                    getattr(segment, "no_speech_prob", None),
                    getattr(segment, "avg_logprob", None),
                    getattr(segment, "compression_ratio", None),
                ):
                    logger.info("Ненадёжный сегмент отброшен: %s", describe_text(text, self._log_text()))
                    self.segment_dropped.emit(text)
                    continue
                parts.append(text)
            if stop_event is not None and stop_event.is_set():
                return ""
            return " ".join(parts).strip()
        except Exception:
            logger.exception("Ошибка распознавания аудио.")
            self.status_changed.emit("Ошибка распознавания речи")
            return ""

    def _next_phrase(self, order: list[str], start: int):
        if not order:
            return None, start
        for offset in range(len(order)):
            index = (start + offset) % len(order)
            source_id = order[index]
            phrases = self._source_queues.get(source_id)
            if phrases is None:
                continue
            try:
                event = phrases.get_nowait()
                return (source_id, event), (index + 1) % len(order)
            except queue.Empty:
                continue
        return None, start

    def _transcribe_loop(self, stop: threading.Event) -> None:
        order = list(self._source_queues)
        cursor = 0
        while self._running and not stop.is_set():
            found, cursor = self._next_phrase(order, cursor)
            if found is None:
                self._phrase_wakeup.wait(0.1)
                self._phrase_wakeup.clear()
                continue
            source_id, event = found
            generation, captured_at, source_label, phrase = event
            age = max(0.0, time.monotonic() - float(captured_at))
            if age > MAX_PENDING_PHRASE_AGE_SECONDS:
                logger.info(
                    "Устаревшая фраза отброшена из источника %s (ожидание %.1f с).",
                    source_id, age,
                )
                continue
            with self._generation_lock:
                current_generation = self._stream_generations.get(source_id, 0)
                source_cancel = self._source_cancel_events[source_id]
            if not self._listening or generation != current_generation:
                continue
            self.source_status_changed.emit(source_id, "Распознаю речь…")
            transcript = self._transcribe(phrase, stop_event=source_cancel)
            if (
                transcript and not stop.is_set() and not source_cancel.is_set()
                and self._listening
                and generation == self._current_stream_generation(source_id)
            ):
                logger.info("Распознано (%s): %s", source_label, describe_text(transcript, self._log_text()))
                self.phrase_detected.emit(transcript)
                self.phrase_detected_with_source.emit(transcript, source_id)
            if not self._running or stop.is_set():
                break

    @staticmethod
    def _to_mono(audio: np.ndarray) -> np.ndarray:
        value = np.asarray(audio, dtype=np.float32)
        if value.ndim > 1:
            value = np.mean(value, axis=1)
        if value.ndim != 1:
            raise ValueError(f"Ожидался аудиомассив frames×channels, получена форма {value.shape}")
        return np.nan_to_num(value, copy=False)

    def _capture_source(
        self,
        source_id: str,
        phrases: queue.Queue,
        stop: threading.Event,
    ) -> None:
        retry = RetryBackoff(RETRY_INITIAL_SECONDS, RETRY_MAX_SECONDS)
        while self._running and not stop.is_set():
            try:
                capture_device, opened_identity, opened_name, follows_default, opened_selection, available_at_open = self._resolve_source(source_id)
                label = f"{SOURCE_LABELS[source_id]}: {opened_name}"
                logger.info("Источник аудио %s открыт: %s (%s)", source_id, opened_name, opened_identity)
                if opened_selection and not available_at_open:
                    source_status = (
                        f"Выбранное устройство недоступно; временно используется: {opened_name}"
                    )
                else:
                    source_status = f"Подключён: {opened_name}"
                self.source_status_changed.emit(source_id, source_status)
                self.status_changed.emit(f"Активен источник: {SOURCE_LABELS[source_id]}")

                segmenter = PhraseSegmenter(
                    CHUNK_DURATION, MIN_SPEECH_DURATION,
                    MAX_PHRASE_DURATION, PREROLL_CHUNKS,
                )
                gate = AdaptiveNoiseGate(
                    base_threshold=float(self.settings.get("silence_threshold", SILENCE_THRESHOLD)),
                    sensitivity=float(self.settings.get("vad_sensitivity", 1.0)),
                    enabled=bool(self.settings.get("adaptive_vad_enabled", True)),
                )
                watcher = DeviceWatcher(DEVICE_CHECK_SECONDS)
                with capture_device.recorder(samplerate=SAMPLE_RATE) as recorder:
                    while self._running and not stop.is_set():
                        if watcher.due():
                            current_default, available_tokens, current_selection = self._current_source_state(source_id)
                            if available_tokens is None:
                                requested_available_now = None
                            elif not current_selection:
                                requested_available_now = True
                            else:
                                requested_available_now = (
                                    current_selection in available_tokens
                                    or current_selection.casefold() in available_tokens
                                )
                            if DeviceWatcher.should_reopen(
                                opened_name=opened_identity,
                                follows_default=follows_default,
                                current_default_name=current_default,
                                opened_selection=opened_selection,
                                current_selection=current_selection,
                                requested_available_at_open=available_at_open,
                                requested_available_now=requested_available_now,
                            ):
                                logger.info("Источник %s изменился; переоткрываю аудиопоток.", source_id)
                                self._invalidate_pending_phrases(source_id=source_id)
                                break

                        audio = recorder.record(numframes=CHUNK_FRAMES)
                        retry.reset()
                        if audio is None or len(audio) == 0:
                            continue
                        if not self._listening or self._is_muted(source_id):
                            segmenter.reset(clear_preroll=True)
                            self._set_source_level(source_id, 0.0)
                            continue

                        mono = self._to_mono(audio)
                        if mono.size == 0:
                            continue
                        rms = float(np.sqrt(np.mean(mono * mono, dtype=np.float64) + 1e-12))
                        self._set_source_level(source_id, min(1.0, rms * 12.0))

                        start_threshold = gate.start_threshold
                        end_threshold = gate.end_threshold
                        activity = speech_activity_ratio(
                            mono, end_threshold,
                            frame_samples=max(1, int(SAMPLE_RATE * 0.02)),
                        )
                        phrase = segmenter.feed(
                            mono, rms, start_threshold,
                            float(self.settings.get("silence_duration", SILENCE_DURATION)),
                            end_threshold=end_threshold,
                            activity_ratio=activity,
                        )
                        # Only learn the noise floor while the segmenter is
                        # outside speech. Long utterances must not teach the
                        # adaptive gate that speech itself is background noise.
                        if not segmenter.in_speech:
                            gate.observe(rms)
                        if phrase is not None:
                            event = (
                                self._current_stream_generation(source_id),
                                time.monotonic(),
                                label,
                                phrase,
                            )
                            if put_drop_oldest(phrases, event):
                                logger.warning("Очередь %s переполнена: отброшена старая фраза.", source_id)
                            self._phrase_wakeup.set()
                if stop.is_set() or not self._running:
                    break
                # The stream was deliberately reopened after a device change.
                segmenter.reset(clear_preroll=True)
                self._set_source_level(source_id, 0.0)
                self._sleep(0.25, stop)
            except Exception as exc:
                self._invalidate_pending_phrases(source_id=source_id)
                logger.exception("Источник аудио %s временно недоступен.", source_id)
                self.source_status_changed.emit(source_id, f"Недоступен: {exc}")
                delay = retry.next_delay()
                self.status_changed.emit(
                    f"{SOURCE_LABELS[source_id]} недоступен. Повтор через {delay:.0f} с…"
                )
                self._set_source_level(source_id, 0.0)
                self._sleep(delay, stop)

        self._invalidate_pending_phrases(source_id=source_id)
        self._set_source_level(source_id, 0.0)
        self.source_status_changed.emit(source_id, "Остановлен")

    def _set_source_level(self, source_id: str, level: float) -> None:
        with self._level_lock:
            self._source_levels[source_id] = float(level)
            combined = max(self._source_levels.values(), default=0.0)
        self.source_audio_level.emit(source_id, float(level))
        self.audio_level.emit(combined)

    def _enabled_sources(self) -> list[str]:
        enabled: list[str] = []
        if bool(self.settings.get("system_audio_enabled", True)):
            enabled.append(SOURCE_SYSTEM)
        if bool(self.settings.get("microphone_enabled", False)):
            enabled.append(SOURCE_MICROPHONE)
        return enabled

    def _report_slow_shutdown(self, thread_name: str, elapsed: float) -> None:
        logger.warning(
            "Остановка аудио задерживается: поток %s работает ещё %.1f с после запроса остановки.",
            thread_name,
            elapsed,
        )
        self.shutdown_delayed.emit(
            f"Остановка аудио задерживается: поток «{thread_name}» ещё работает "
            f"({elapsed:.0f} с). Безопасно ожидаю его завершения."
        )

    def run(self) -> None:
        transcriber: threading.Thread | None = None
        transcriber_stop = self._transcriber_stop_event
        try:
            if not self._running:
                return
            enabled_sources = self._enabled_sources()
            if not enabled_sources:
                self.status_changed.emit("Все аудиоисточники отключены в настройках")
                return

            self.status_changed.emit("Загружаю модель распознавания…")
            self._load_whisper()
            if not self._running:
                return

            self._source_queues = {
                source_id: queue.Queue(maxsize=3) for source_id in enabled_sources
            }
            self._phrases_queue = self._source_queues.get(SOURCE_SYSTEM)
            transcriber = threading.Thread(
                target=self._transcribe_loop,
                args=(transcriber_stop,),
                name="whisper-transcriber",
                daemon=False,
            )
            transcriber.start()

            for source_id in enabled_sources:
                if not self._running:
                    break
                source_stop = threading.Event()
                capture_thread = threading.Thread(
                    target=self._capture_source,
                    args=(source_id, self._source_queues[source_id], source_stop),
                    name=f"audio-capture-{source_id}",
                    daemon=False,
                )
                with self._lifecycle_lock:
                    self._source_stop_events[source_id] = source_stop
                    self._capture_threads[source_id] = capture_thread
                capture_thread.start()

            enabled_labels = " и ".join(SOURCE_LABELS[item] for item in enabled_sources)
            self.status_changed.emit(f"Прослушиваю: {enabled_labels}")

            while self._running:
                time.sleep(0.1)
        except Exception as exc:
            logger.exception("Аудиосистема не запущена.")
            self.status_changed.emit(f"Ошибка аудио: {exc}")
        finally:
            self._running = False
            with self._lifecycle_lock:
                source_stops = list(self._source_stop_events.values())
                capture_threads = list(self._capture_threads.values())
            for event in source_stops:
                event.set()
            for thread in capture_threads:
                if thread.is_alive():
                    join_thread_with_warning(
                        thread,
                        warning_after=SHUTDOWN_WARNING_AFTER_SECONDS,
                        poll_interval=SHUTDOWN_JOIN_POLL_SECONDS,
                        on_slow=self._report_slow_shutdown,
                    )
            transcriber_stop.set()
            with self._generation_lock:
                for event in self._source_cancel_events.values():
                    event.set()
            self._phrase_wakeup.set()
            if transcriber is not None and transcriber is not threading.current_thread():
                join_thread_with_warning(
                    transcriber,
                    warning_after=SHUTDOWN_WARNING_AFTER_SECONDS,
                    poll_interval=SHUTDOWN_JOIN_POLL_SECONDS,
                    on_slow=self._report_slow_shutdown,
                )
            with self._lifecycle_lock:
                self._source_stop_events.clear()
                self._capture_threads.clear()
            self._invalidate_pending_phrases()
