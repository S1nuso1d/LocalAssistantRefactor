"""Open a folder in Explorer, warning first if the screen may be shared."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox


def open_folder(parent, path: str | Path, protection_enabled: bool) -> bool:
    """Open *path* in the file manager. Returns True if it was opened.

    Explorer is another program's window, so capture protection of this app
    does not hide it. While protection is enabled, ask before opening it.
    """
    if protection_enabled:
        answer = QMessageBox.question(
            parent,
            "Открыть папку",
            "Папка откроется в Проводнике Windows. Это окно другой программы, "
            "и оно будет видно при демонстрации или записи экрана — защита "
            "LocalAssistant на него не распространяется.\n\nОткрыть папку?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return False
    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
