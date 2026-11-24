"""Asynchronous, privacy-conscious diagnostics center for the desktop UI."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QApplication, QHeaderView,
)

from .diagnostics_core import collect_diagnostics, format_diagnostic_report
from .settings_store import app_data_dir

logger = logging.getLogger("live_assistant.diagnostics")


class _DiagnosticsWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, snapshot: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.snapshot = snapshot

    def run(self) -> None:
        try:
            report = collect_diagnostics(**self.snapshot)
            self.completed.emit(report)
        except Exception as exc:
            logger.exception("Не удалось собрать диагностический отчёт")
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class DiagnosticsDialog(QDialog):
    def __init__(
        self,
        settings: dict[str, Any],
        conversation_db: str | Path,
        knowledge_db: str | Path,
        audio_status: dict[str, str],
        listening: bool,
        speaking: bool,
        build_version: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._settings = dict(settings)
        self._conversation_db = str(conversation_db)
        self._knowledge_db = str(knowledge_db)
        self._audio_status = dict(audio_status)
        self._listening = bool(listening)
        self._speaking = bool(speaking)
        self._build_version = str(build_version)
        self._worker: _DiagnosticsWorker | None = None
        self._report: dict[str, Any] | None = None
        self._closing_after_check = False

        self.setWindowTitle("Центр диагностики LocalAssistant")
        self.setMinimumSize(720, 560)
        self.resize(850, 660)
        self.setModal(True)

        root = QVBoxLayout(self)
        title = QLabel("Центр диагностики")
        title.setStyleSheet("font-size: 17pt; font-weight: 700;")
        subtitle = QLabel(
            "Проверка Ollama, аудио, моделей и локальных баз данных. "
            "Текст диалогов и содержимое логов в отчёт не включаются."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #9BA6C3;")
        root.addWidget(title)
        root.addWidget(subtitle)

        self.summary = QLabel("Проверка ещё не выполнялась")
        self.summary.setStyleSheet("padding: 9px; background: #222A3D; border-radius: 8px; font-weight: 600;")
        root.addWidget(self.summary)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Раздел", "Проверка", "Состояние", "Подробности"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        self.footer = QLabel("Отчёт не содержит содержимое переписки, документов или распознанной речи.")
        self.footer.setStyleSheet("color: #8994B0; font-size: 8pt;")
        self.footer.setWordWrap(True)
        root.addWidget(self.footer)

        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Обновить проверки")
        self.copy_button = QPushButton("Копировать отчёт")
        self.copy_button.setEnabled(False)
        self.open_folder_button = QPushButton("Открыть папку данных")
        close_button = QPushButton("Закрыть")
        self.refresh_button.clicked.connect(self.refresh)
        self.copy_button.clicked.connect(self.copy_report)
        self.open_folder_button.clicked.connect(self.open_data_folder)
        close_button.clicked.connect(self.close)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.open_folder_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

        self.refresh()

    def refresh(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        snapshot = {
            "settings": self._settings,
            "audio_status": self._audio_status,
            "listening": self._listening,
            "speaking": self._speaking,
            "conversation_db": self._conversation_db,
            "knowledge_db": self._knowledge_db,
            "app_data": app_data_dir(),
        }
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Проверяю…")
        self.summary.setText("Выполняется проверка; интерфейс остаётся доступным…")
        self._worker = _DiagnosticsWorker(snapshot, self)
        self._worker.completed.connect(self._on_completed)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_completed(self, report: dict[str, Any]) -> None:
        self._report = report
        rows = report.get("rows", [])
        self.table.setRowCount(len(rows))
        labels = {"ok": "В норме", "info": "Информация", "warning": "Внимание", "error": "Ошибка", "not-created": "Не создана"}
        for row_index, row in enumerate(rows):
            values = (row.get("group", ""), row.get("name", ""), labels.get(row.get("status", ""), row.get("status", "")), row.get("detail", ""))
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row_index, column, item)
        summary = report.get("summary", {})
        problems = sum(row.get("status") in {"error", "warning"} for row in rows)
        if problems:
            self.summary.setText(f"Проверка завершена · замечаний: {problems}")
        else:
            self.summary.setText("Проверка завершена · ошибок не обнаружено")
        self.copy_button.setEnabled(True)
        self.footer.setText(
            f"Установленные модели Ollama: {len(report.get('models', []))}. "
            "В отчёт не включаются тексты переписки, документы, память и содержимое логов."
        )

    def _on_failed(self, message: str) -> None:
        self.summary.setText("Не удалось завершить диагностику")
        self.table.setRowCount(0)
        self.copy_button.setEnabled(False)
        self.footer.setText(message[:500])

    def _on_worker_finished(self) -> None:
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Обновить проверки")
        if self._closing_after_check:
            self._closing_after_check = False
            self.close()

    def copy_report(self) -> None:
        if self._report is None:
            return
        QApplication.clipboard().setText(
            format_diagnostic_report(self._report, self._build_version)
        )
        self.footer.setText("Диагностический отчёт скопирован. Он не содержит текст диалогов.")

    def open_data_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_data_dir())))

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._closing_after_check = True
            event.ignore()
            self.footer.setText("Окно закроется после безопасного завершения текущей проверки.")
            return
        super().closeEvent(event)
