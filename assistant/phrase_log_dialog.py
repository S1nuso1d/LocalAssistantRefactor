"""Non-modal window showing the filter's decisions for recent phrases."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout,
)

from .history_dialog import DIALOG_STYLE
from .phrase_log import PhraseLog

ACCEPTED = QColor("#A8E8C9")
SKIPPED = QColor("#E9ECF8")
MUTED = QColor("#8F98B2")


class PhraseLogDialog(QDialog):
    def __init__(self, parent, log: PhraseLog, on_answer: Callable[[str], None]):
        super().__init__(parent)
        self._log = log
        self._on_answer = on_answer

        self.setWindowTitle("Журнал фильтра")
        self.setMinimumSize(480, 480)
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setStyleSheet(DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        title = QLabel("Журнал фильтра")
        title.setStyleSheet("font-size: 15pt; font-weight: 700; color: white;")
        root.addWidget(title)

        self.summary = QLabel("")
        self.summary.setObjectName("hint")
        root.addWidget(self.summary)

        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setWordWrap(True)
        root.addWidget(self.list, 1)

        hint = QLabel(
            "Здесь видно, какие фразы услышал ассистент и почему ответил или "
            "промолчал. Если фраза пропущена зря — ответьте на неё вручную "
            "и поменяйте режим фильтра в настройках."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        buttons = QHBoxLayout()
        clear = QPushButton("Очистить")
        clear.clicked.connect(self._clear)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.hide)
        self.answer_button = QPushButton("Ответить на эту фразу")
        self.answer_button.setObjectName("primary")
        self.answer_button.clicked.connect(self._answer_selected)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        buttons.addWidget(close)
        buttons.addWidget(self.answer_button)
        root.addLayout(buttons)

        self.list.itemDoubleClicked.connect(lambda _i: self._answer_selected())
        self.list.currentItemChanged.connect(lambda *_: self._sync_button())
        self.reload()

    def reload(self) -> None:
        selected = self.list.currentRow()
        self.list.clear()
        for decision in self._log.items():
            clock = datetime.fromtimestamp(decision.timestamp).strftime("%H:%M:%S")
            mark = "✓" if decision.accepted else "—"
            item = QListWidgetItem(
                f"{mark}  {clock}  {decision.text}\n      {decision.reason}"
            )
            item.setData(Qt.UserRole, decision.text)
            item.setForeground(ACCEPTED if decision.accepted else SKIPPED)
            item.setToolTip(decision.reason)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(max(0, min(selected, self.list.count() - 1)))
        accepted, skipped = self._log.stats()
        self.summary.setText(
            f"Принято: {accepted} · пропущено: {skipped} (последние {accepted + skipped})"
        )
        self._sync_button()

    def _sync_button(self) -> None:
        self.answer_button.setEnabled(self.list.currentItem() is not None)

    def _clear(self) -> None:
        self._log.clear()
        self.reload()

    def _answer_selected(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self._on_answer(str(item.data(Qt.UserRole)))
