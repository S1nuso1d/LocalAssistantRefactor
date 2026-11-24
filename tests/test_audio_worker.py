"""Audio runtime integration tests; mocked hardware, real worker logic."""
from __future__ import annotations

import os
import queue
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    from PySide6.QtCore import QCoreApplication
    _QT_APP = QCoreApplication.instance() or QCoreApplication([])
except Exception:
    QCoreApplication = None
    _QT_APP = None

if os.environ.get("LOCALASSISTANT_FULL_AUDIO_TESTS") == "1":
    from assistant.audio_worker import AudioWorker, SOURCE_MICROPHONE, SOURCE_SYSTEM
    from assistant.settings_store import SettingsStore
    HAVE_AUDIO_RUNTIME = True
else:
    try:
        from assistant.audio_worker import AudioWorker, SOURCE_MICROPHONE, SOURCE_SYSTEM
        from assistant.settings_store import SettingsStore
        HAVE_AUDIO_RUNTIME = True
    except Exception:
        AudioWorker = None
        SettingsStore = None
        SOURCE_MICROPHONE = "microphone"
        SOURCE_SYSTEM = "system"
        HAVE_AUDIO_RUNTIME = False


@unittest.skipUnless(HAVE_AUDIO_RUNTIME, "audio runtime dependencies are not installed")
class AudioWorkerMultiSourceTests(unittest.TestCase):
    def _wait_for(self, condition, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _QT_APP is not None:
                _QT_APP.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        if _QT_APP is not None:
            _QT_APP.processEvents()
        return bool(condition())

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.settings = SettingsStore(Path(self.temp_dir.name) / "settings.json")
        self.worker = AudioWorker(self.settings)

    def test_transcriber_selects_sources_round_robin(self):
        system_queue = queue.Queue(maxsize=3)
        mic_queue = queue.Queue(maxsize=3)
        self.worker._source_queues = {
            SOURCE_SYSTEM: system_queue,
            SOURCE_MICROPHONE: mic_queue,
        }
        system_queue.put((0, "system", "system-phrase-1"))
        system_queue.put((0, "system", "system-phrase-2"))
        mic_queue.put((0, "mic", "mic-phrase-1"))
        mic_queue.put((0, "mic", "mic-phrase-2"))

        first, cursor = self.worker._next_phrase(
            [SOURCE_SYSTEM, SOURCE_MICROPHONE], 0
        )
        second, cursor = self.worker._next_phrase(
            [SOURCE_SYSTEM, SOURCE_MICROPHONE], cursor
        )
        third, cursor = self.worker._next_phrase(
            [SOURCE_SYSTEM, SOURCE_MICROPHONE], cursor
        )
        fourth, cursor = self.worker._next_phrase(
            [SOURCE_SYSTEM, SOURCE_MICROPHONE], cursor
        )

        self.assertEqual(
            [first[0], second[0], third[0], fourth[0]],
            [SOURCE_SYSTEM, SOURCE_MICROPHONE, SOURCE_SYSTEM, SOURCE_MICROPHONE],
        )

    def test_invalidating_one_source_preserves_other_source_queue(self):
        system_queue = queue.Queue(maxsize=3)
        mic_queue = queue.Queue(maxsize=3)
        self.worker._source_queues = {
            SOURCE_SYSTEM: system_queue,
            SOURCE_MICROPHONE: mic_queue,
        }
        system_generation = self.worker._current_stream_generation(SOURCE_SYSTEM)
        mic_generation = self.worker._current_stream_generation(SOURCE_MICROPHONE)
        system_queue.put((system_generation, time.monotonic(), "system", object()))
        mic_queue.put((mic_generation, time.monotonic(), "mic", object()))

        self.worker._invalidate_pending_phrases(source_id=SOURCE_SYSTEM)

        self.assertTrue(system_queue.empty())
        self.assertFalse(mic_queue.empty())
        self.assertNotEqual(system_generation, self.worker._current_stream_generation(SOURCE_SYSTEM))
        self.assertEqual(mic_generation, self.worker._current_stream_generation(SOURCE_MICROPHONE))

    def test_transcriber_drops_result_if_source_changes_mid_transcription(self):
        phrases: queue.Queue = queue.Queue(maxsize=3)
        self.worker._source_queues = {SOURCE_SYSTEM: phrases}
        stop = threading.Event()
        transcribing = threading.Event()
        release = threading.Event()
        received: list[tuple[str, str]] = []
        generation = self.worker._current_stream_generation(SOURCE_SYSTEM)
        phrases.put((generation, time.monotonic(), "Системный звук: Speakers", object()))

        def slow_transcribe(_phrase, stop_event=None):
            transcribing.set()
            release.wait(2)
            return "старый текст"

        self.worker._transcribe = slow_transcribe
        self.worker.phrase_detected_with_source.connect(
            lambda text, source: received.append((text, source))
        )
        thread = threading.Thread(
            target=self.worker._transcribe_loop, args=(stop,), daemon=True
        )
        thread.start()
        self.assertTrue(transcribing.wait(1))
        self.worker._invalidate_pending_phrases(source_id=SOURCE_SYSTEM)
        release.set()
        time.sleep(0.05)
        self.worker._running = False
        stop.set()
        self.worker._phrase_wakeup.set()
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(received, [])

    def test_transcriber_discards_phrase_that_waited_too_long(self):
        phrases: queue.Queue = queue.Queue(maxsize=3)
        self.worker._source_queues = {SOURCE_SYSTEM: phrases}
        stop = threading.Event()
        received: list[str] = []
        generation = self.worker._current_stream_generation(SOURCE_SYSTEM)
        phrases.put((generation, time.monotonic() - 60.0, "system", object()))
        self.worker._transcribe = lambda *_args, **_kwargs: self.fail(
            "stale phrase must not enter Whisper"
        )
        self.worker.phrase_detected_with_source.connect(
            lambda text, _source: received.append(text)
        )

        thread = threading.Thread(
            target=self.worker._transcribe_loop, args=(stop,), daemon=True
        )
        thread.start()
        deadline = time.monotonic() + 0.5
        while not phrases.empty() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.worker._running = False
        stop.set()
        self.worker._phrase_wakeup.set()
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(received, [])

    def test_transcriber_emits_source_aware_phrase(self):
        phrases: queue.Queue = queue.Queue(maxsize=3)
        self.worker._source_queues = {SOURCE_MICROPHONE: phrases}
        stop = threading.Event()
        received: list[tuple[str, str]] = []
        generation = self.worker._current_stream_generation(SOURCE_MICROPHONE)
        phrases.put((generation, time.monotonic(), "Микрофон: USB Mic", object()))
        self.worker._transcribe = lambda *_args, **_kwargs: "проверь микрофон"
        self.worker.phrase_detected_with_source.connect(
            lambda text, source: received.append((text, source))
        )

        thread = threading.Thread(
            target=self.worker._transcribe_loop, args=(stop,), daemon=True
        )
        thread.start()
        self.assertTrue(self._wait_for(lambda: bool(received), timeout=1.0))
        self.worker._running = False
        stop.set()
        self.worker._phrase_wakeup.set()
        thread.join(timeout=1)

        self.assertEqual(received, [("проверь микрофон", SOURCE_MICROPHONE)])

    def test_microphone_resolves_pinned_device_by_backend_id(self):
        self.settings.update({
            "microphone_device_id": "usb-mic-42",
            "microphone_device": "USB Microphone",
        })
        mic = SimpleNamespace(id="usb-mic-42", name="USB Microphone")
        default = SimpleNamespace(id="default-mic", name="Default Microphone")
        with (
            patch("assistant.audio_worker.sc.all_microphones", return_value=[mic, default]),
            patch("assistant.audio_worker.sc.default_microphone", return_value=default),
        ):
            capture, identity, name, follows, selected, available = self.worker._resolve_source(SOURCE_MICROPHONE)
        self.assertIs(capture, mic)
        self.assertEqual(identity, "usb-mic-42")
        self.assertEqual(name, "USB Microphone")
        self.assertFalse(follows)
        self.assertEqual(selected, "usb-mic-42")
        self.assertTrue(available)

    def test_loopback_uses_backend_id_then_name_fallback(self):
        speaker = SimpleNamespace(id="speaker-7", name="Speakers")
        default = speaker
        loopback = SimpleNamespace(name="Speakers (loopback)")
        with (
            patch("assistant.audio_worker.sc.all_speakers", return_value=[speaker]),
            patch("assistant.audio_worker.sc.default_speaker", return_value=default),
            patch("assistant.audio_worker.sc.get_microphone", return_value=loopback) as get_mic,
        ):
            result = self.worker._loopback_microphone()
        self.assertIs(result[0], loopback)
        self.assertEqual(result[1], "Speakers")
        self.assertEqual(get_mic.call_args.kwargs["id"], "speaker-7")
        self.assertTrue(result[2])

    def test_missing_pinned_microphone_falls_back_but_keeps_requested_identity(self):
        self.settings.update({
            "microphone_device_id": "removed-id",
            "microphone_device": "Removed Microphone",
        })
        default = SimpleNamespace(id="default-id", name="Default Microphone")
        with (
            patch("assistant.audio_worker.sc.all_microphones", return_value=[default]),
            patch("assistant.audio_worker.sc.default_microphone", return_value=default),
        ):
            resolved = self.worker._resolve_source(SOURCE_MICROPHONE)
        self.assertIs(resolved[0], default)
        self.assertTrue(resolved[3])
        self.assertEqual(resolved[4], "removed-id")
        self.assertFalse(resolved[5])

    def test_capture_thread_stops_cleanly_when_recorder_returns(self):
        stop = threading.Event()
        phrases = queue.Queue(maxsize=3)
        class Recorder:
            def record(self, numframes):
                stop.set()
                return [[0.0, 0.0]] * numframes

        class Microphone:
            @contextmanager
            def recorder(self, samplerate):
                yield Recorder()

        with patch.object(
            self.worker,
            "_resolve_source",
            return_value=(Microphone(), "speaker-id", "Speakers", False, "speaker-id", True),
        ):
            self.worker._running = True
            self.worker._capture_source(SOURCE_SYSTEM, phrases, stop)

        self.assertTrue(stop.is_set())
        self.assertTrue(phrases.empty())

    def test_system_and_microphone_are_muted_during_tts_by_default(self):
        self.worker.set_speaking(True)
        self.assertTrue(self.worker._is_muted(SOURCE_SYSTEM))
        self.assertTrue(self.worker._is_muted(SOURCE_MICROPHONE))

    def test_microphone_can_remain_active_during_tts_when_explicitly_enabled(self):
        self.settings.update({"microphone_during_tts": True})
        self.worker.set_speaking(True)
        self.assertTrue(self.worker._is_muted(SOURCE_SYSTEM))
        self.assertFalse(self.worker._is_muted(SOURCE_MICROPHONE))

    def test_combined_level_uses_highest_active_source(self):
        values = []
        self.worker.audio_level.connect(values.append)
        self.worker._set_source_level(SOURCE_SYSTEM, 0.25)
        self.worker._set_source_level(SOURCE_MICROPHONE, 0.75)
        self.worker._set_source_level(SOURCE_MICROPHONE, 0.0)
        self.assertEqual(values, [0.25, 0.75, 0.25])


if __name__ == "__main__":
    unittest.main()
