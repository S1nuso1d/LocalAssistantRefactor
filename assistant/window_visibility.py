"""Pure helpers for hiding the application's secondary windows."""
from __future__ import annotations

from typing import Iterable


def dismiss_open_dialogs(dialogs: Iterable) -> int:
    """Close every visible dialog and return how many were asked to close.

    Hiding only the main window (tray click, "hide" hotkey) would leave modal
    dialogs such as Settings or the Knowledge Base on screen. ``close()`` is
    used instead of ``hide()`` so each dialog's own close handling still runs
    (background jobs are never abandoned, and a dialog that must finish work
    first may stay open until it is done).
    """
    closed = 0
    for dialog in list(dialogs):
        try:
            if dialog.isVisible():
                dialog.close()
                closed += 1
        except RuntimeError:
            continue  # the wrapped C++ object is already gone
    return closed


def file_dialog_uses_native(protection_enabled: bool) -> bool:
    """A native Windows file dialog is a separate OS window that capture
    protection cannot reach; use Qt's own dialog while protection is on."""
    return not protection_enabled
