from __future__ import annotations

import unittest

from assistant.audio_device_labels import format_device_options


class AudioDeviceLabelTests(unittest.TestCase):
    def test_unique_device_keeps_clean_display_name(self):
        self.assertEqual(
            format_device_options([("endpoint-1", "USB Headset")]),
            [("USB Headset", "endpoint-1", "USB Headset")],
        )

    def test_duplicate_names_are_disambiguated_by_short_backend_id(self):
        result = format_device_options([
            ("wasapi-render-12345678", "USB Headset"),
            ("wasapi-capture-87654321", "USB Headset"),
        ])
        self.assertEqual(result[0][0], "USB Headset · …12345678")
        self.assertEqual(result[1][0], "USB Headset · …87654321")
        self.assertEqual([item[1] for item in result], [
            "wasapi-render-12345678", "wasapi-capture-87654321"
        ])
        self.assertEqual([item[2] for item in result], [
            "USB Headset", "USB Headset"
        ])

    def test_duplicate_names_are_case_insensitive(self):
        result = format_device_options([
            ("first-id", "Headphones"),
            ("second-id", "headphones"),
        ])
        self.assertIn("…first-id", result[0][0])
        self.assertIn("…second-id", result[1][0])

    def test_colliding_short_ids_use_full_ids(self):
        result = format_device_options([
            ("render-abcdefgh", "Headset"),
            ("capture-abcdefgh", "Headset"),
        ])
        self.assertEqual(result[0][0], "Headset · render-abcdefgh")
        self.assertEqual(result[1][0], "Headset · capture-abcdefgh")

    def test_devices_without_backend_ids_get_stable_ordinals(self):
        result = format_device_options([
            ("", "Headset"),
            ("", "Headset"),
        ])
        self.assertEqual(result[0][0], "Headset · #1")
        self.assertEqual(result[1][0], "Headset · #2")

    def test_empty_names_are_ignored(self):
        self.assertEqual(format_device_options([("id", "  ")]), [])


if __name__ == "__main__":
    unittest.main()
