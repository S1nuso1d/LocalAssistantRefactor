"""Tests for safe, observable worker-thread shutdown."""
from __future__ import annotations

import threading
import unittest

from assistant.thread_lifecycle import join_thread_with_warning


class ThreadLifecycleTests(unittest.TestCase):
    def test_completed_thread_does_not_emit_slow_shutdown_warning(self):
        thread = threading.Thread(target=lambda: None, name="quick-worker")
        thread.start()
        thread.join(timeout=1)

        warnings = []
        warned = join_thread_with_warning(
            thread,
            warning_after=0.01,
            poll_interval=0.005,
            on_slow=lambda name, elapsed: warnings.append((name, elapsed)),
        )

        self.assertFalse(warned)
        self.assertEqual(warnings, [])
        self.assertFalse(thread.is_alive())

    def test_slow_thread_is_reported_once_and_still_joined(self):
        release = threading.Event()
        started = threading.Event()

        def work():
            started.set()
            release.wait()

        thread = threading.Thread(target=work, name="blocked-worker")
        thread.start()
        self.assertTrue(started.wait(timeout=1))

        warnings = []

        def report(name, elapsed):
            warnings.append((name, elapsed))
            release.set()

        warned = join_thread_with_warning(
            thread,
            warning_after=0.02,
            poll_interval=0.005,
            on_slow=report,
        )

        self.assertTrue(warned)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0][0], "blocked-worker")
        self.assertGreaterEqual(warnings[0][1], 0.02)
        self.assertFalse(thread.is_alive())

    def test_invalid_timing_values_are_rejected(self):
        thread = threading.Thread(target=lambda: None)
        with self.assertRaises(ValueError):
            join_thread_with_warning(thread, warning_after=-1, poll_interval=0.1)
        with self.assertRaises(ValueError):
            join_thread_with_warning(thread, warning_after=1, poll_interval=0)


if __name__ == "__main__":
    unittest.main()
