"""Hardware-independent tests for source selection and adaptive VAD."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from assistant.audio_sources import (
    DEFAULT_DEVICE_LABEL,
    device_identity,
    device_matches,
    is_requested_device_available,
    resolve_device,
)
from assistant.segmenter import AdaptiveNoiseGate, PhraseSegmenter
from assistant.settings_store import SettingsStore, coerce_setting


class AudioDeviceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.usb = SimpleNamespace(id="wasapi-usb-001", name="USB Headset")
        self.bluetooth = SimpleNamespace(id="wasapi-bt-002", name="Bluetooth Headphones")
        self.devices = [self.usb, self.bluetooth]

    def test_backend_id_is_preferred_over_display_name(self):
        self.assertEqual(device_identity(self.usb), "wasapi-usb-001")
        self.assertTrue(device_matches(self.usb, "wasapi-usb-001", "Old name"))
        self.assertFalse(device_matches(self.bluetooth, "wasapi-usb-001", "Old name"))
        # A stale ID must not silently bind to an unrelated endpoint with a
        # reused display name.
        replacement = SimpleNamespace(id="replacement-id", name="USB Headset")
        self.assertFalse(device_matches(replacement, "wasapi-usb-001", "USB Headset"))

    def test_pinned_device_is_resolved_by_backend_id(self):
        selection = resolve_device(
            self.devices, self.bluetooth, "wasapi-usb-001", "USB Headset"
        )
        self.assertIs(selection.device, self.usb)
        self.assertFalse(selection.follows_default)
        self.assertTrue(selection.requested_available)

    def test_missing_pinned_device_falls_back_but_is_remembered(self):
        selection = resolve_device(
            [self.bluetooth], self.bluetooth, "removed-device", "Old USB Headset"
        )
        self.assertIs(selection.device, self.bluetooth)
        self.assertTrue(selection.follows_default)
        self.assertFalse(selection.requested_available)
        self.assertEqual(selection.requested_id, "removed-device")

    def test_default_selection_uses_default_without_becoming_pinned(self):
        selection = resolve_device(self.devices, self.bluetooth, "", DEFAULT_DEVICE_LABEL)
        self.assertIs(selection.device, self.bluetooth)
        self.assertTrue(selection.follows_default)
        self.assertTrue(selection.requested_available)

    def test_availability_checks_id_and_legacy_name(self):
        self.assertTrue(is_requested_device_available(self.devices, "wasapi-usb-001", ""))
        self.assertTrue(is_requested_device_available(self.devices, "", "USB Headset"))
        self.assertFalse(is_requested_device_available(self.devices, "removed", "Old Headset"))


class AudioSettingsMigrationTests(unittest.TestCase):
    def test_microphone_is_opt_in_and_system_audio_keeps_legacy_default(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsStore(Path(folder) / "settings.json")
            self.assertTrue(settings.get("system_audio_enabled"))
            self.assertFalse(settings.get("microphone_enabled"))
            self.assertFalse(settings.get("microphone_during_tts"))
            self.assertTrue(settings.get("adaptive_vad_enabled"))

    def test_legacy_name_based_device_setting_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(
                json.dumps({"audio_device": "USB Headset"}), encoding="utf-8"
            )
            settings = SettingsStore(path)
            self.assertEqual(settings.get("audio_device"), "USB Headset")
            self.assertEqual(settings.get("audio_device_id"), "")

    def test_new_audio_settings_are_validated(self):
        self.assertEqual(coerce_setting("vad_sensitivity", 9), 2.0)
        self.assertEqual(coerce_setting("microphone_enabled", "invalid"), False)
        self.assertEqual(coerce_setting("microphone_during_tts", "invalid"), False)
        self.assertEqual(coerce_setting("audio_device_id", "  endpoint-1  "), "endpoint-1")


class AdaptiveNoiseGateTests(unittest.TestCase):
    def test_static_mode_preserves_legacy_threshold(self):
        gate = AdaptiveNoiseGate(base_threshold=0.01, enabled=False)
        for value in (0.001, 0.02, 0.04):
            gate.observe(value)
        self.assertAlmostEqual(gate.start_threshold, 0.01)
        self.assertAlmostEqual(gate.end_threshold, 0.01)

    def test_higher_sensitivity_lowers_start_threshold(self):
        normal = AdaptiveNoiseGate(base_threshold=0.009, sensitivity=1.0)
        high = AdaptiveNoiseGate(base_threshold=0.009, sensitivity=2.0)
        self.assertLess(high.start_threshold, normal.start_threshold)

    def test_noise_floor_adapts_up_and_threshold_remains_bounded(self):
        gate = AdaptiveNoiseGate(base_threshold=0.008, sensitivity=1.0)
        initial = gate.start_threshold
        for _ in range(120):
            gate.observe(0.02)
        self.assertGreater(gate.noise_floor, 0.003)
        self.assertGreater(gate.start_threshold, initial)
        self.assertLessEqual(gate.start_threshold, 0.05)

    def test_noise_floor_tracks_quieter_environment(self):
        gate = AdaptiveNoiseGate(base_threshold=0.008, sensitivity=1.0)
        for _ in range(120):
            gate.observe(0.02)
        loud_threshold = gate.start_threshold
        for _ in range(120):
            gate.observe(0.001)
        self.assertLess(gate.start_threshold, loud_threshold)

    def test_segmenter_uses_hysteresis_to_keep_quiet_syllable_connected(self):
        segmenter = PhraseSegmenter(
            chunk_duration=0.5, min_speech=0.5, max_phrase=5.0, preroll_chunks=0
        )
        high = np.ones(8, dtype=np.float32)
        quiet = np.ones(8, dtype=np.float32)
        self.assertIsNone(segmenter.feed(high, 0.02, 0.01, 1.0, end_threshold=0.005))
        self.assertTrue(segmenter.in_speech)
        self.assertIsNone(segmenter.feed(quiet, 0.007, 0.01, 1.0, end_threshold=0.005))
        self.assertTrue(segmenter.in_speech)


if __name__ == "__main__":
    unittest.main()
