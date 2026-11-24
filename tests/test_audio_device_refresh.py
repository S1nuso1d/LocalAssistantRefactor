"""Tests for non-blocking audio endpoint discovery."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from assistant.settings_dialog import _AudioDevicesWorker


class AudioDevicesWorkerTests(unittest.TestCase):
    def _run_worker(self):
        worker = _AudioDevicesWorker()
        results = []
        worker.loaded.connect(
            lambda speakers, microphones, errors: results.append(
                (speakers, microphones, errors)
            )
        )
        worker.run()
        self.assertEqual(len(results), 1)
        return results[0]

    def test_enumerates_speakers_and_microphones(self):
        speaker = SimpleNamespace(id="speaker-1", name="USB Speakers")
        microphone = SimpleNamespace(id="mic-1", name="USB Microphone")
        with (
            patch("assistant.settings_dialog.sc.all_speakers", return_value=[speaker]),
            patch(
                "assistant.settings_dialog.sc.all_microphones",
                return_value=[microphone],
            ) as all_microphones,
        ):
            speakers, microphones, errors = self._run_worker()

        self.assertEqual(speakers, [("speaker-1", "USB Speakers")])
        self.assertEqual(microphones, [("mic-1", "USB Microphone")])
        self.assertEqual(errors, [])
        all_microphones.assert_called_once_with(include_loopback=False)

    def test_speaker_failure_does_not_hide_microphones(self):
        microphone = SimpleNamespace(id="mic-2", name="Built-in Mic")
        with (
            patch(
                "assistant.settings_dialog.sc.all_speakers",
                side_effect=OSError("speaker backend failed"),
            ),
            patch(
                "assistant.settings_dialog.sc.all_microphones",
                return_value=[microphone],
            ),
        ):
            speakers, microphones, errors = self._run_worker()

        self.assertEqual(speakers, [])
        self.assertEqual(microphones, [("mic-2", "Built-in Mic")])
        self.assertEqual(errors, ["вывод: OSError"])

    def test_microphone_failure_does_not_hide_speakers(self):
        speaker = SimpleNamespace(id="speaker-2", name="HDMI Output")
        with (
            patch("assistant.settings_dialog.sc.all_speakers", return_value=[speaker]),
            patch(
                "assistant.settings_dialog.sc.all_microphones",
                side_effect=RuntimeError("microphone backend failed"),
            ),
        ):
            speakers, microphones, errors = self._run_worker()

        self.assertEqual(speakers, [("speaker-2", "HDMI Output")])
        self.assertEqual(microphones, [])
        self.assertEqual(errors, ["микрофон: RuntimeError"])

    def test_empty_and_invalid_names_are_filtered(self):
        devices = [
            SimpleNamespace(id="good", name="Device"),
            SimpleNamespace(id="empty", name="  "),
            SimpleNamespace(name="No ID"),
        ]
        with (
            patch("assistant.settings_dialog.sc.all_speakers", return_value=devices),
            patch("assistant.settings_dialog.sc.all_microphones", return_value=[]),
        ):
            speakers, microphones, errors = self._run_worker()

        self.assertEqual(speakers, [("good", "Device"), ("", "No ID")])
        self.assertEqual(microphones, [])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
