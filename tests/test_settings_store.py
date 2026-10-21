"""Tests for persisted appearance and tray settings."""
import json
import tempfile
import unittest
from pathlib import Path

from assistant.settings_store import DEFAULT_SETTINGS, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def test_new_appearance_settings_have_defaults(self):
        self.assertIn("background_saturation", DEFAULT_SETTINGS)
        self.assertIn("close_to_tray", DEFAULT_SETTINGS)
        self.assertEqual(DEFAULT_SETTINGS["background_saturation"], 65)
        self.assertFalse(DEFAULT_SETTINGS["close_to_tray"])

    def test_appearance_and_tray_settings_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            first = SettingsStore(path)
            first.update({
                "background_saturation": 42,
                "close_to_tray": True,
            })

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["background_saturation"], 42)
            self.assertIs(saved["close_to_tray"], True)

            second = SettingsStore(path)
            self.assertEqual(second.get("background_saturation"), 42)
            self.assertTrue(second.get("close_to_tray"))


if __name__ == "__main__":
    unittest.main()
