"""Apply Windows capture affinity to every native top-level app window.

The guard observes native-handle creation and window show events so dialogs and
other top-level widgets receive the same privacy setting as the main window.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget

from . import winintegration as win

logger = logging.getLogger("live_assistant.capture")

_WATCHED = (QEvent.Type.WinIdChange, QEvent.Type.Show)
_SUCCESS_STATES = (win.HIDDEN, win.BLACK)


class CaptureGuard(QObject):
    def __init__(self, enabled: bool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._enabled = bool(enabled)
        self._touched: set[int] = set()
        self._last_states: dict[int, str] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        # This filter is installed application-wide, so it must never let a
        # normal Python exception escape through Qt's C++ event dispatcher.
        try:
            if (
                event.type() in _WATCHED
                and isinstance(obj, QWidget)
                and obj.isWindow()
            ):
                self._apply(obj, self._enabled)
        except RuntimeError:
            # Qt may deliver a final event while a wrapped C++ object is being
            # destroyed. There is no useful work left to do for that widget.
            pass
        except Exception:
            logger.exception("Ошибка event filter защиты захвата экрана.")
        return False

    def _apply(self, widget: QWidget, hidden: bool) -> str | None:
        try:
            hwnd = int(widget.winId())
        except (RuntimeError, TypeError, ValueError):
            return None

        if not hidden and hwnd not in self._touched:
            return None

        previous = self._last_states.get(hwnd)
        if hidden and hwnd in self._touched and previous in _SUCCESS_STATES:
            # The HWND has already been protected successfully. Avoid calling
            # the Win32 API for every repeated Show event.
            return previous

        state = win.set_capture_hidden(hwnd, hidden)
        if hidden:
            self._touched.add(hwnd)
        self._last_states[hwnd] = state

        if state in (win.FAILED, win.BLACK) and state != previous:
            logger.warning(
                "Состояние защиты захвата: %s; widget=%s; hwnd=0x%X",
                state, type(widget).__name__, hwnd,
            )
        elif state == win.HIDDEN and state != previous:
            logger.info(
                "Окно исключено из захвата: widget=%s; hwnd=0x%X",
                type(widget).__name__, hwnd,
            )
        return state

    def set_enabled(self, enabled: bool) -> None:
        """Change protection and apply it to currently visible top-level windows."""
        self._enabled = bool(enabled)
        try:
            widgets = QApplication.topLevelWidgets()
        except RuntimeError:
            return

        for widget in widgets:
            try:
                if widget.isWindow() and widget.isVisible():
                    self._apply(widget, self._enabled)
            except RuntimeError:
                continue

    @staticmethod
    def state_of(widget: QWidget) -> str:
        """Return the actual affinity read back from Windows for this widget."""
        try:
            return win.get_capture_state(int(widget.winId()))
        except (RuntimeError, TypeError, ValueError):
            return win.FAILED
