"""Multiline chat composer with Enter-to-send and Shift+Enter for a newline."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPlainTextEdit


class ChatInput(QPlainTextEdit):
    send_requested = Signal()

    MIN_HEIGHT = 46
    MAX_HEIGHT = 140

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("Напишите сообщение…")
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setMaximumHeight(self.MAX_HEIGHT)
        self.setTabChangesFocus(False)
        self.textChanged.connect(self._adjust_height)
        self._adjust_height()

    def keyPressEvent(self, event) -> None:
        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and not (event.modifiers() & Qt.ShiftModifier)
        ):
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _adjust_height(self) -> None:
        document_height = self.document().documentLayout().documentSize().height()
        desired = int(document_height) + self.frameWidth() * 2 + 16
        self.setFixedHeight(max(self.MIN_HEIGHT, min(self.MAX_HEIGHT, desired)))
