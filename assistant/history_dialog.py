"""Searchable conversation history popup (replaces input-dialog + menu)."""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout,
)

from .text_utils import relative_day

DIALOG_STYLE = """
QDialog { background: #171B29; color: #F3F5FF; font-family: 'Segoe UI'; }
QLabel { color: #E6EBFA; }
QLabel#hint { color: #8F98B2; font-size: 8pt; }
QLineEdit {
    background: #0D1120; color: #F5F7FF; border: 1px solid #414B68;
    border-radius: 10px; padding: 9px 12px; selection-background-color: #667CE0;
}
QLineEdit:focus { border-color: #8A9BFF; }
QListWidget {
    background: transparent; border: none; outline: none;
}
QListWidget::item {
    padding: 9px 12px; margin: 2px 0; border-radius: 10px; color: #E8EAF5;
}
QListWidget::item:hover { background: rgba(140, 155, 240, 28); }
QListWidget::item:selected { background: rgba(122, 138, 255, 80); color: white; }
QPushButton {
    background: #3B4563; color: white; border: 1px solid #596685;
    border-radius: 8px; padding: 8px 14px;
}
QPushButton:hover { background: #46517A; }
QPushButton#primary { background: #6B7CE4; border-color: #9FAEFF; }
QPushButton#primary:hover { background: #7C8CF0; }
"""


class HistoryDialog(QDialog):
    def __init__(
        self,
        parent,
        fetch: Callable[[str], list[dict[str, Any]]],
        current_id: int | None,
    ) -> None:
        super().__init__(parent)
        self._fetch = fetch
        self._current_id = current_id
        self.selected_id: int | None = None

        self.setWindowTitle("История диалогов")
        self.setMinimumSize(440, 520)
        self.setModal(True)
        self.setStyleSheet(DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        title = QLabel("История диалогов")
        title.setStyleSheet("font-size: 15pt; font-weight: 700; color: white;")
        root.addWidget(title)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названиям и тексту сообщений…")
        self.search.setClearButtonEnabled(True)
        root.addWidget(self.search)

        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        root.addWidget(self.list, 1)

        self.hint = QLabel("Enter — открыть · Esc — закрыть")
        self.hint.setObjectName("hint")
        root.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        self.open_button = QPushButton("Открыть")
        self.open_button.setObjectName("primary")
        self.open_button.setDefault(True)
        self.open_button.clicked.connect(self._open_current)
        buttons.addWidget(close)
        buttons.addWidget(self.open_button)
        root.addLayout(buttons)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self._reload)
        self.search.textChanged.connect(lambda _t: self._debounce.start())
        self.search.returnPressed.connect(self._open_current)
        self.list.itemActivated.connect(lambda _i: self._open_current())
        self.list.itemDoubleClicked.connect(lambda _i: self._open_current())

        self._reload()
        self.search.setFocus()

    def keyPressEvent(self, event) -> None:
        # Let the arrow keys drive the list while typing in the search box.
        if event.key() in (Qt.Key_Down, Qt.Key_Up) and self.list.count():
            row = self.list.currentRow()
            row += 1 if event.key() == Qt.Key_Down else -1
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
            event.accept()
            return
        super().keyPressEvent(event)

    def _reload(self) -> None:
        query = self.search.text().strip()
        try:
            items = self._fetch(query)
        except Exception:
            items = []
        self.list.clear()
        for item in items[:100]:
            conv_id = int(item["id"])
            mark = "✓  " if conv_id == self._current_id else ""
            title = str(item.get("title", "Диалог"))[:70]
            when = relative_day(float(item.get("updated_at") or 0))
            entry = QListWidgetItem(f"{mark}{title}\n{when}")
            entry.setData(Qt.UserRole, conv_id)
            entry.setToolTip(str(item.get("title", "")))
            self.list.addItem(entry)
        if self.list.count():
            self.list.setCurrentRow(0)
            self.hint.setText("Enter — открыть · ↑↓ — выбор · Esc — закрыть")
        else:
            self.hint.setText(
                "Ничего не найдено" if query else "История пока пуста"
            )
        self.open_button.setEnabled(self.list.count() > 0)

    def _open_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.selected_id = int(item.data(Qt.UserRole))
        self.accept()
