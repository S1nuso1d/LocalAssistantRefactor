"""Regression tests for capture diagnostics and graceful console shutdown."""
from __future__ import annotations

import unittest

from assistant.shutdown import install_console_interrupt_handler
from assistant.winintegration import format_win32_error


class Win32ErrorFormattingTests(unittest.TestCase):
    def test_zero_error_code_is_reported_as_unavailable(self):
        self.assertIn("не предоставила код ошибки", format_win32_error(0))

    def test_nonzero_error_code_is_preserved(self):
        self.assertIn("WinError 5", format_win32_error(5))


class FakeSignalApi:
    SIGINT = 2

    def __init__(self):
        self.previous_handler = object()
        self.handler = None
        self.registered_signal = None

    def getsignal(self, signum):
        self.assert_signal(signum)
        return self.previous_handler

    def signal(self, signum, handler):
        self.assert_signal(signum)
        self.registered_signal = signum
        self.handler = handler

    def assert_signal(self, signum):
        if signum != self.SIGINT:
            raise AssertionError(f"Unexpected signal: {signum}")


class ConsoleInterruptTests(unittest.TestCase):
    def test_ctrl_c_schedules_graceful_shutdown_once(self):
        api = FakeSignalApi()
        scheduled = []
        callback = lambda: None

        previous = install_console_interrupt_handler(
            callback,
            schedule=scheduled.append,
            signal_api=api,
        )

        self.assertIs(previous, api.previous_handler)
        self.assertEqual(api.registered_signal, api.SIGINT)
        self.assertIsNotNone(api.handler)

        api.handler(api.SIGINT, None)
        api.handler(api.SIGINT, None)

        self.assertEqual(scheduled, [callback])


if __name__ == "__main__":
    unittest.main()
