"""Tests for non-blocking audio endpoint discovery."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from assistant.settings_dialog import _AudioDevicesWorker


class AudioDevicesWorkerTests(unittest.TestCase):
    def _run_worker(self, soundcard_module):
        worker = _AudioDevicesWorker(soundcard_module)
        results = []
        worker.loaded.connect(
            lambda speakers, microphones, errors: results.append(
                (speakers, microphones, errors)
            )
        )
        worker.run()
        self.assertEqual(len(results), 1)
        return results[0], soundcard_module

    def test_enumerates_speakers_and_microphones(self):
        speaker = SimpleNamespace(id="speaker-1", name="USB Speakers")
        microphone = SimpleNamespace(id="mic-1", name="USB Microphone")
        soundcard = SimpleNamespace(
            all_speakers=Mock(return_value=[speaker]),
            all_microphones=Mock(return_value=[microphone]),
        )
        (speakers, microphones, errors), soundcard = self._run_worker(soundcard)

        self.assertEqual(speakers, [("speaker-1", "USB Speakers")])
        self.assertEqual(microphones, [("mic-1", "USB Microphone")])
        self.assertEqual(errors, [])
        soundcard.all_microphones.assert_called_once_with(include_loopback=False)

    def test_speaker_failure_does_not_hide_microphones(self):
        microphone = SimpleNamespace(id="mic-2", name="Built-in Mic")
        soundcard = SimpleNamespace(
            all_speakers=Mock(side_effect=OSError("speaker backend failed")),
            all_microphones=Mock(return_value=[microphone]),
        )
        (speakers, microphones, errors), _ = self._run_worker(soundcard)

        self.assertEqual(speakers, [])
        self.assertEqual(microphones, [("mic-2", "Built-in Mic")])
        self.assertEqual(errors, ["вывод: OSError"])

    def test_microphone_failure_does_not_hide_speakers(self):
        speaker = SimpleNamespace(id="speaker-2", name="HDMI Output")
        soundcard = SimpleNamespace(
            all_speakers=Mock(return_value=[speaker]),
            all_microphones=Mock(
                side_effect=RuntimeError("microphone backend failed")
            ),
        )
        (speakers, microphones, errors), _ = self._run_worker(soundcard)

        self.assertEqual(speakers, [("speaker-2", "HDMI Output")])
        self.assertEqual(microphones, [])
        self.assertEqual(errors, ["микрофон: RuntimeError"])

    def test_empty_and_invalid_names_are_filtered(self):
        devices = [
            SimpleNamespace(id="good", name="Device"),
            SimpleNamespace(id="empty", name="  "),
            SimpleNamespace(name="No ID"),
        ]
        soundcard = SimpleNamespace(
            all_speakers=Mock(return_value=devices),
            all_microphones=Mock(return_value=[]),
        )
        (speakers, microphones, errors), _ = self._run_worker(soundcard)

        self.assertEqual(speakers, [("good", "Device"), ("", "No ID")])
        self.assertEqual(microphones, [])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
