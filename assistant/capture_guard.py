"""Hide *every* top-level window of the app from screen capture.

Hiding only the main window is not enough: dialogs, menus, combo-box popups
and tooltips are separate native windows, and any of them would show up in a
shared screen. An application-wide event filter applies the protection to
each window the moment its native handle is created or it is shown.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget

from . import winintegration as win

logger = logging.getLogger("live_assistant.capture")

_WATCHED = (QEvent.Type.WinIdChange, QEvent.Type.Show)


class CaptureGuard(QObject):
    def __init__(self, enabled: bool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._enabled = bool(enabled)
        self._touched: set[int] = set()  # windows we have hidden at least once

    @property
    def enabled(self) -> bool:
        return self._enabled

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        # Keep this path tiny: it runs for every event in the application.
        if event.type() in _WATCHED and isinstance(obj, QWidget) and obj.isWindow():
            self._apply(obj, self._enabled)
        return False

    def _apply(self, widget: QWidget, hidden: bool) -> str | None:
        try:
            hwnd = int(widget.winId())
        except Exception:
            return None
        if not hidden and hwnd not in self._touched:
            return None  # nothing to undo
        state = win.set_capture_hidden(hwnd, hidden)
        if hidden:
            self._touched.add(hwnd)
        if state in (win.FAILED, win.BLACK):
            logger.warning("Скрытие окна от записи: %s", state)
        return state

    def set_enabled(self, enabled: bool) -> None:
        """Switch protection on/off for windows that are already on screen."""
        self._enabled = bool(enabled)
        for widget in QApplication.topLevelWidgets():
            if widget.isWindow() and widget.isVisible():
                self._apply(widget, self._enabled)

    @staticmethod
    def state_of(widget: QWidget) -> str:
        """Affinity actually in force for *widget* right now."""
        try:
            return win.get_capture_state(int(widget.winId()))
        except Exception:
            return win.FAILED
