"""Regression tests for screen-capture protection and window hiding (r24)."""
from __future__ import annotations

import io
import unittest

from assistant import winintegration as win
from assistant.shutdown import console_attached
from assistant.window_visibility import dismiss_open_dialogs, file_dialog_uses_native


class FakeAffinityApi:
    """Records which HWNDs currently have protection, like the real OS."""

    def __init__(self):
        self.protected: set[int] = set()
        self.calls: list[tuple[int, bool]] = []

    def __call__(self, hwnd: int, hidden: bool) -> str:
        self.calls.append((hwnd, hidden))
        if hidden:
            self.protected.add(hwnd)
            return win.HIDDEN
        self.protected.discard(hwnd)
        return win.VISIBLE

    def destroy_window(self, hwnd: int) -> None:
        """A destroyed window loses its affinity; the HWND value is free."""
        self.protected.discard(hwnd)


class HwndReuseTests(unittest.TestCase):
    def test_recycled_hwnd_is_protected_again(self):
        """The bug: a new window that receives a recycled HWND must not be
        treated as already protected."""
        cache, api = win.AffinityCache(), FakeAffinityApi()

        # Window A (a tooltip) is shown and protected under HWND 0x100.
        win.apply_protection(cache, 0x100, True, force=True, apply=api)
        self.assertIn(0x100, api.protected)

        # A is destroyed; Windows hands the same HWND to a new window B.
        api.destroy_window(0x100)
        self.assertNotIn(0x100, api.protected)

        # Qt reports a native handle for B (WinIdChange => force=True).
        state, _ = win.apply_protection(cache, 0x100, True, force=True, apply=api)
        self.assertEqual(state, win.HIDDEN)
        self.assertIn(0x100, api.protected, "recycled HWND was left unprotected")

    def test_without_force_the_stale_cache_would_skip_the_new_window(self):
        """Documents why force is required: this is the old behaviour."""
        cache, api = win.AffinityCache(), FakeAffinityApi()
        win.apply_protection(cache, 0x200, True, force=True, apply=api)
        api.destroy_window(0x200)
        win.apply_protection(cache, 0x200, True, force=False, apply=api)
        self.assertNotIn(0x200, api.protected)  # skipped -> would be visible

    def test_repeated_show_events_do_not_repeat_win32_calls(self):
        cache, api = win.AffinityCache(), FakeAffinityApi()
        win.apply_protection(cache, 0x300, True, force=True, apply=api)
        for _ in range(5):
            win.apply_protection(cache, 0x300, True, apply=api)
        self.assertEqual(api.calls, [(0x300, True)])

    def test_failed_protection_is_retried_on_next_show(self):
        cache = win.AffinityCache()
        results = iter([win.FAILED, win.HIDDEN])
        calls = []

        def flaky(hwnd, hidden):
            calls.append(hwnd)
            return next(results)

        self.assertEqual(win.apply_protection(cache, 7, True, apply=flaky)[0], win.FAILED)
        self.assertEqual(win.apply_protection(cache, 7, True, apply=flaky)[0], win.HIDDEN)
        self.assertEqual(len(calls), 2)

    def test_turning_protection_off_resets_only_protected_windows(self):
        cache, api = win.AffinityCache(), FakeAffinityApi()
        win.apply_protection(cache, 1, True, apply=api)
        # Window 2 was never protected: nothing to undo.
        self.assertEqual(win.apply_protection(cache, 2, False, apply=api), (None, None))
        self.assertEqual(win.apply_protection(cache, 1, False, apply=api)[0], win.VISIBLE)
        self.assertEqual(api.protected, set())

    def test_black_rectangle_mode_counts_as_protected(self):
        cache = win.AffinityCache()
        calls = []

        def legacy(hwnd, hidden):
            calls.append(hwnd)
            return win.BLACK

        win.apply_protection(cache, 9, True, apply=legacy)
        win.apply_protection(cache, 9, True, apply=legacy)
        self.assertEqual(len(calls), 1)

    def test_cache_is_bounded(self):
        cache = win.AffinityCache(limit=4)
        for hwnd in range(100):
            cache.record(hwnd, win.HIDDEN, hidden=True)
        self.assertLessEqual(len(cache._states), 4)
        # After an eviction a window is simply re-protected on its next Show.
        api = FakeAffinityApi()
        state, _ = win.apply_protection(cache, 0, True, apply=api)
        self.assertEqual(state, win.HIDDEN)


    def test_cache_eviction_keeps_handles_tracked_until_unprotected(self):
        cache, api = win.AffinityCache(limit=4), FakeAffinityApi()

        # Fill the state cache and force its eviction with a fifth HWND.
        for hwnd in range(5):
            win.apply_protection(cache, hwnd, True, apply=api)

        # The oldest handle is still protected by Windows and must remain
        # tracked even though its state snapshot was evicted.
        self.assertIn(0, api.protected)
        self.assertTrue(cache.touched(0))

        state, _ = win.apply_protection(cache, 0, False, apply=api)
        self.assertEqual(state, win.VISIBLE)
        self.assertNotIn(0, api.protected)
        self.assertFalse(cache.touched(0))

        # A successfully reset handle no longer needs repeated API calls.
        self.assertEqual(
            win.apply_protection(cache, 0, False, apply=api),
            (None, None),
        )

    def test_failed_unprotection_keeps_handle_tracked_for_retry(self):
        cache = win.AffinityCache()
        win.apply_protection(cache, 10, True, apply=lambda hwnd, hidden: win.HIDDEN)

        outcomes = iter([win.FAILED, win.VISIBLE])

        def reset(hwnd, hidden):
            return next(outcomes)

        state, _ = win.apply_protection(cache, 10, False, apply=reset)
        self.assertEqual(state, win.FAILED)
        self.assertTrue(cache.touched(10))

        state, _ = win.apply_protection(cache, 10, False, apply=reset)
        self.assertEqual(state, win.VISIBLE)
        self.assertFalse(cache.touched(10))


class FakeDialog:
    def __init__(self, visible=True, raises=False):
        self.visible, self.raises = visible, raises
        self.closed = 0

    def isVisible(self):
        if self.raises:
            raise RuntimeError("wrapped C++ object has been deleted")
        return self.visible

    def close(self):
        self.closed += 1
        self.visible = False


class DismissDialogTests(unittest.TestCase):
    def test_closes_only_visible_dialogs_and_counts_them(self):
        shown, hidden = FakeDialog(True), FakeDialog(False)
        self.assertEqual(dismiss_open_dialogs([shown, hidden]), 1)
        self.assertEqual((shown.closed, hidden.closed), (1, 0))

    def test_deleted_dialog_does_not_break_hiding(self):
        dead, alive = FakeDialog(raises=True), FakeDialog(True)
        self.assertEqual(dismiss_open_dialogs([dead, alive]), 1)
        self.assertEqual(alive.closed, 1)

    def test_nothing_to_close(self):
        self.assertEqual(dismiss_open_dialogs([]), 0)

    def test_uses_close_not_hide_so_dialog_cleanup_runs(self):
        class HideForbidden(FakeDialog):
            def hide(self):
                raise AssertionError("hide() would skip the dialog's close handling")
        dialog = HideForbidden(True)
        dismiss_open_dialogs([dialog])
        self.assertEqual(dialog.closed, 1)


class FileDialogPolicyTests(unittest.TestCase):
    def test_native_dialog_only_when_protection_is_off(self):
        self.assertFalse(file_dialog_uses_native(True))
        self.assertTrue(file_dialog_uses_native(False))


class ConsoleDetectionTests(unittest.TestCase):
    def test_interactive_console(self):
        class Tty(io.StringIO):
            def isatty(self):
                return True
        self.assertTrue(console_attached(Tty()))

    def test_redirected_or_missing_stream(self):
        self.assertFalse(console_attached(io.StringIO()))
        self.assertFalse(console_attached(_NoStream()))

    def test_closed_stream_is_not_a_console(self):
        stream = io.StringIO()
        stream.close()
        self.assertFalse(console_attached(stream))


class _NoStream:
    """Mimics sys.stdout under pythonw/windowed builds (no isatty)."""


if __name__ == "__main__":
    unittest.main()
