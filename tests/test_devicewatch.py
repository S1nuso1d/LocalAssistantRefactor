"""Deterministic tests for audio-device monitoring and retry scheduling."""
from __future__ import annotations

import unittest

from assistant.devicewatch import DeviceWatcher, RetryBackoff


class DeviceWatcherTests(unittest.TestCase):
    def test_polling_is_rate_limited(self):
        now = [10.0]
        watcher = DeviceWatcher(interval=4.0, clock=lambda: now[0])

        self.assertFalse(watcher.due())
        now[0] += 3.9
        self.assertFalse(watcher.due())
        now[0] += 0.1
        self.assertTrue(watcher.due())
        self.assertFalse(watcher.due())

    def test_default_device_change_reopens_only_default_following_stream(self):
        self.assertTrue(
            DeviceWatcher.default_changed(True, "speaker-old", "speaker-new")
        )
        self.assertFalse(
            DeviceWatcher.default_changed(False, "speaker-old", "speaker-new")
        )
        self.assertFalse(
            DeviceWatcher.default_changed(True, "speaker-old", "speaker-old")
        )

    def test_reappearing_pinned_device_reopens_fallback_stream(self):
        self.assertTrue(
            DeviceWatcher.should_reopen(
                opened_name="default-speaker",
                follows_default=True,
                current_default_name="default-speaker",
                opened_selection="pinned-headset",
                current_selection="pinned-headset",
                requested_available_at_open=False,
                requested_available_now=True,
            )
        )

    def test_disappearing_pinned_device_reopens_its_stream(self):
        self.assertTrue(
            DeviceWatcher.should_reopen(
                opened_name="pinned-headset",
                follows_default=False,
                current_default_name="default-speaker",
                opened_selection="pinned-headset",
                current_selection="pinned-headset",
                requested_available_at_open=True,
                requested_available_now=False,
            )
        )

    def test_failed_device_enumeration_does_not_force_reopen(self):
        self.assertFalse(
            DeviceWatcher.should_reopen(
                opened_name="pinned-headset",
                follows_default=False,
                current_default_name=None,
                opened_selection="pinned-headset",
                current_selection="pinned-headset",
                requested_available_at_open=True,
                requested_available_now=None,
            )
        )

    def test_retry_backoff_grows_to_cap_and_resets_after_success(self):
        retry = RetryBackoff(initial_seconds=1.0, maximum_seconds=15.0)
        self.assertEqual(
            [retry.next_delay() for _ in range(6)],
            [1.0, 2.0, 4.0, 8.0, 15.0, 15.0],
        )
        retry.reset()
        self.assertEqual(retry.next_delay(), 1.0)


if __name__ == "__main__":
    unittest.main()
