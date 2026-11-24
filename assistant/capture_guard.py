"""Apply Windows capture affinity to every native top-level app window.

The guard observes native-handle creation and window show events so dialogs and
other top-level widgets receive the same privacy setting as the main window.
"""
from __future__ import annotations

import logging
import weakref

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
        self._cache = win.AffinityCache()
        self._watched_widget_ids: set[int] = set()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        # This filter is installed application-wide, so it must never let a
        # normal Python exception escape through Qt's C++ event dispatcher.
        try:
            event_type = event.type()
            if (
                event_type in _WATCHED
                and isinstance(obj, QWidget)
                and obj.isWindow()
            ):
                # WinIdChange means a *new* native window: Windows may have
                # recycled an HWND we cached for a destroyed window, so the
                # cache must not be trusted for it.
                self._apply(
                    obj, self._enabled,
                    force=event_type == QEvent.Type.WinIdChange,
                )
        except RuntimeError:
            # Qt may deliver a final event while a wrapped C++ object is being
            # destroyed. There is no useful work left to do for that widget.
            pass
        except Exception:
            logger.exception("Ошибка event filter защиты захвата экрана.")
        return False

    def _forget_widget(self, owner_id: int) -> None:
        self._cache.forget_owner(owner_id)
        self._watched_widget_ids.discard(owner_id)

    def _watch_widget_lifecycle(self, widget: QWidget, owner_id: int) -> None:
        if owner_id in self._watched_widget_ids:
            return

        guard_ref = weakref.ref(self)

        def on_destroyed(*_args, token: int = owner_id) -> None:
            guard = guard_ref()
            if guard is not None:
                guard._forget_widget(token)

        try:
            widget.destroyed.connect(on_destroyed)
        except (RuntimeError, TypeError):
            # Keep the handle tracked: dropping it would prevent a safe reset.
            logger.exception(
                "Не удалось подписаться на уничтожение QWidget id=%s.", owner_id
            )
            return
        self._watched_widget_ids.add(owner_id)

    def _apply(self, widget: QWidget, hidden: bool, force: bool = False) -> str | None:
        try:
            hwnd = int(widget.winId())
        except (RuntimeError, TypeError, ValueError):
            return None

        owner_id = id(widget)
        if force:
            # WinIdChange invalidates all handles previously owned by this widget.
            self._cache.forget_owner(owner_id)

        state, previous = win.apply_protection(
            self._cache, hwnd, hidden, force=force, owner=owner_id
        )
        if self._cache.owns(owner_id):
            self._watch_widget_lifecycle(widget, owner_id)
        if state is None:
            return None

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
