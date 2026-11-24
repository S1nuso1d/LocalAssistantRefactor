"""Pure helpers for hiding and restoring the application's secondary windows."""
from __future__ import annotations

from typing import Iterable


def dismiss_open_dialogs(dialogs: Iterable) -> int:
    """Dismiss visible dialogs, preserving busy dialogs that opt into tray hiding.

    Most dialogs are closed normally so their cleanup handlers run. A busy
    dialog may expose hide_for_tray and return True to stay alive while its
    background operation continues.
    """
    handled = 0
    for dialog in list(dialogs):
        try:
            if not dialog.isVisible():
                continue
            hide_for_tray = getattr(dialog, "hide_for_tray", None)
            if callable(hide_for_tray) and hide_for_tray():
                handled += 1
                continue
            dialog.close()
            handled += 1
        except RuntimeError:
            continue  # the wrapped C++ object is already gone
    return handled


def restore_tray_dialogs(dialogs: Iterable) -> int:
    """Restore dialogs that were temporarily hidden by hide_for_tray."""
    restored = 0
    for dialog in list(dialogs):
        try:
            restore = getattr(dialog, "restore_from_tray", None)
            if callable(restore) and restore():
                restored += 1
        except RuntimeError:
            continue  # the wrapped C++ object is already gone
    return restored


def file_dialog_uses_native(protection_enabled: bool) -> bool:
    """A native Windows file dialog is a separate OS window that capture
    protection cannot reach; use Qt's own dialog while protection is on."""
    return not protection_enabled
