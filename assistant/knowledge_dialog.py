"""Qt management UI for local documents and explicit long-term memories."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout,
    QWidget,
)

from .embeddings import EmbeddingError
from .knowledge_base import KnowledgeBaseStore, KnowledgeCancelled, KnowledgeError
from .settings_store import SettingsStore
from .window_visibility import file_dialog_uses_native

logger = logging.getLogger("live_assistant.knowledge_ui")


class _DocumentImportWorker(QThread):
    progress = Signal(str)
    completed = Signal(object)

    def __init__(
        self,
        paths: list[str],
        settings: SettingsStore,
        *,
        embedder: Callable[..., list[list[float]]] | None = None,
        document_ids: list[int] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.paths = list(paths)
        self.settings = settings
        self.embedder = embedder
        self.document_ids = [int(value) for value in (document_ids or [])]
        self.cancel_event = threading.Event()
        self.final_results: list[dict] = []

    def _index_embeddings(self, store: KnowledgeBaseStore, document_id: int, model: str) -> int:
        if self.embedder is None:
            raise EmbeddingError(
                "Общий клиент Ollama не передан. Повторно откройте окно базы знаний из LocalAssistant."
            )
        return store.index_document_embeddings(
            document_id,
            model=model,
            embed=lambda texts: self.embedder(
                texts, model=model, cancel_event=self.cancel_event
            ),
            cancel_event=self.cancel_event,
        )

    def request_cancel(self) -> None:
        self.cancel_event.set()
        self.requestInterruption()

    def run(self) -> None:
        results: list[dict] = []
        try:
            store = KnowledgeBaseStore()
            embedding_model = str(self.settings.get("embedding_model", "")).strip()
            should_index_new = bool(self.settings.get("semantic_search_enabled", False))

            if self.document_ids:
                documents = {int(item["id"]): item for item in store.list_documents()}
                jobs = [
                    (document_id, documents[document_id].get("name", f"Документ {document_id}"))
                    for document_id in self.document_ids if document_id in documents
                ]
                missing = [value for value in self.document_ids if value not in documents]
                results.extend(
                    {"ok": False, "name": f"Документ {value}", "error": "Документ уже удалён из базы знаний."}
                    for value in missing
                )
                for index, (document_id, name) in enumerate(jobs, start=1):
                    if self.cancel_event.is_set():
                        results.append({"ok": True, "status": "cancelled", "name": name,
                                        "warning": "Операция отменена; оставшиеся документы не затронуты."})
                        break
                    self.progress.emit(f"Создание embeddings {index}/{len(jobs)}: {name}")
                    try:
                        if not embedding_model:
                            raise KnowledgeError("Сначала выберите модель эмбеддингов в Настройки → Модели.")
                        count = self._index_embeddings(store, document_id, embedding_model)
                        results.append({"ok": True, "status": "indexed", "document_id": document_id,
                                        "name": name, "embeddings": count})
                    except Exception as exc:
                        if self.cancel_event.is_set():
                            results.append({"ok": True, "status": "cancelled", "document_id": document_id,
                                            "name": name,
                                            "warning": "Индексация отменена; предыдущий индекс, если он был, сохранён."})
                            break
                        logger.exception("Не удалось индексировать embeddings для %s", name)
                        results.append({"ok": False, "name": name, "error": str(exc)})
            else:
                for index, path in enumerate(self.paths, start=1):
                    name = Path(path).name
                    if self.cancel_event.is_set():
                        results.append({"ok": True, "status": "cancelled", "name": name,
                                        "warning": "Операция отменена; этот и следующие файлы не импортировались."})
                        break
                    self.progress.emit(f"Импорт {index}/{len(self.paths)}: {name}")
                    try:
                        result = store.import_document(path, cancel_event=self.cancel_event)
                        # Importing text/FTS and creating embeddings are two
                        # separate durable steps.  If cancellation arrives
                        # after the document transaction commits, report the
                        # successful lexical import instead of claiming the
                        # document failed (which could prompt a duplicate retry).
                        if result["status"] == "imported" and embedding_model and should_index_new:
                            if self.cancel_event.is_set():
                                result["embedding_warning"] = (
                                    "Индексация отменена; полнотекстовый поиск доступен."
                                )
                            else:
                                try:
                                    result["embeddings"] = self._index_embeddings(
                                        store, int(result["document_id"]), embedding_model
                                    )
                                except Exception as exc:
                                    if self.cancel_event.is_set():
                                        result["embedding_warning"] = (
                                            "Индексация отменена; полнотекстовый поиск доступен."
                                        )
                                    else:
                                        result["embedding_warning"] = str(exc)
                                        logger.warning("Не удалось создать embeddings для %s: %s", name, exc)
                        results.append({"ok": True, **result})
                        if self.cancel_event.is_set():
                            break
                    except KnowledgeCancelled as exc:
                        results.append({"ok": True, "status": "cancelled", "name": name,
                                        "warning": str(exc)})
                        break
                    except Exception as exc:
                        logger.exception("Не удалось импортировать документ %s", name)
                        results.append({"ok": False, "name": name, "error": str(exc)})
        except Exception as exc:
            logger.exception("Не удалось запустить задачу базы знаний")
            results.append({"ok": False, "name": "База знаний", "error": str(exc)})
        finally:
            self.final_results = results
            self.completed.emit(results)


class KnowledgeBaseDialog(QDialog):
    def __init__(
        self,
        settings: SettingsStore,
        embedder: Callable[..., list[list[float]]] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.embedder = embedder
        self.store = KnowledgeBaseStore()
        self._worker: _DocumentImportWorker | None = None
        self._selected_memory_id: int | None = None
        self._hidden_for_tray = False

        self.setWindowTitle("Локальная база знаний и память")
        self.setMinimumSize(760, 590)
        self.resize(900, 680)

        root = QVBoxLayout(self)
        title = QLabel("Локальная база знаний")
        title.setStyleSheet("font-size: 16pt; font-weight: 700;")
        hint = QLabel(
            "Документы и сохранённые заметки остаются в локальной SQLite-базе. "
            "Они передаются модели только как справочный контекст для ответа."
        )
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        root.addWidget(title)
        root.addWidget(hint)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self._build_documents_tab()
        self._build_memories_tab()
        self._build_search_tab()

        self.status = QLabel("Готово")
        self.status.setObjectName("hint")
        root.addWidget(self.status)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.accept)
        close_row.addWidget(close_button)
        root.addLayout(close_row)

        try:
            self._refresh_documents()
            self._refresh_memories()
        except Exception as exc:
            logger.exception("Не удалось прочитать локальную базу знаний")
            QMessageBox.critical(self, "База знаний", f"Не удалось прочитать локальную базу знаний:\n{exc}")
        self.setStyleSheet("""
            QDialog { background: #171B29; color: #F3F5FF; font-family: 'Segoe UI'; }
            QLabel { color: #E6EBFA; }
            QLabel#hint { color: #9AA5C0; }
            QLineEdit, QPlainTextEdit, QTableWidget {
                background: #0D1120; color: #F5F7FF; border: 1px solid #414B68;
                border-radius: 7px; padding: 6px;
            }
            QPushButton { background: #3B4563; color: white; border: 1px solid #596685; border-radius: 8px; padding: 8px 12px; }
            QPushButton:hover { background: #46517A; }
            QTabWidget::pane { border: 1px solid #2C3350; border-radius: 8px; }
            QTabBar::tab { color: #AEB9D0; padding: 8px 14px; }
            QTabBar::tab:selected { color: white; border-bottom: 2px solid #8A9BFF; }
        """)

    def _build_documents_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        description = QLabel(
            "Поддерживаются PDF с текстовым слоем, DOCX, TXT и Markdown. "
            "Сканированные PDF без текстового слоя не распознаются: OCR не включён."
        )
        description.setWordWrap(True)
        description.setObjectName("hint")
        layout.addWidget(description)

        self.documents_table = QTableWidget(0, 4)
        self.documents_table.setHorizontalHeaderLabels(["Документ", "Фрагменты", "Размер", "Индекс эмбеддингов"])
        self.documents_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.documents_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.documents_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.documents_table.horizontalHeader().setStretchLastSection(True)
        self.documents_table.horizontalHeader().setSectionResizeMode(0, self.documents_table.horizontalHeader().ResizeMode.Stretch)
        layout.addWidget(self.documents_table, 1)

        buttons = QHBoxLayout()
        self.add_documents_button = QPushButton("Добавить документы…")
        self.add_documents_button.clicked.connect(self._choose_documents)
        self.reindex_document_button = QPushButton("Переиндексировать embeddings")
        self.reindex_document_button.setToolTip(
            "Создать или обновить семантический индекс выбранного документа. "
            "Текущий индекс останется доступен, если новая индексация завершится ошибкой."
        )
        self.reindex_document_button.clicked.connect(self._reindex_selected_document)
        self.remove_document_button = QPushButton("Удалить выбранный")
        self.remove_document_button.clicked.connect(self._remove_selected_document)
        refresh = QPushButton("Обновить список")
        refresh.clicked.connect(self._refresh_documents)
        buttons.addWidget(self.add_documents_button)
        buttons.addWidget(self.reindex_document_button)
        buttons.addWidget(self.remove_document_button)
        buttons.addStretch(1)
        buttons.addWidget(refresh)
        layout.addLayout(buttons)

        progress_row = QHBoxLayout()
        self.import_progress = QProgressBar()
        self.import_progress.setRange(0, 0)
        self.import_progress.hide()
        self.cancel_import_button = QPushButton("Отменить")
        self.cancel_import_button.setEnabled(False)
        self.cancel_import_button.clicked.connect(self._cancel_worker)
        progress_row.addWidget(self.import_progress, 1)
        progress_row.addWidget(self.cancel_import_button)
        layout.addLayout(progress_row)
        self.tabs.addTab(page, "Документы")

    def _build_memories_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        description = QLabel(
            "Долговременная память управляется явно: сохраняй только те сведения, "
            "которые действительно хочешь использовать в будущих диалогах."
        )
        description.setWordWrap(True)
        description.setObjectName("hint")
        layout.addWidget(description)

        self.memories_table = QTableWidget(0, 3)
        self.memories_table.setHorizontalHeaderLabels(["Название", "Категория", "Обновлено"])
        self.memories_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.memories_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.memories_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.memories_table.horizontalHeader().setStretchLastSection(True)
        self.memories_table.horizontalHeader().setSectionResizeMode(0, self.memories_table.horizontalHeader().ResizeMode.Stretch)
        self.memories_table.itemSelectionChanged.connect(self._load_selected_memory)
        layout.addWidget(self.memories_table, 1)

        form_widget = QWidget()
        form = QFormLayout(form_widget)
        self.memory_title = QLineEdit()
        self.memory_category = QLineEdit()
        self.memory_tags = QLineEdit()
        self.memory_content = QPlainTextEdit()
        self.memory_content.setPlaceholderText("Что именно следует помнить?")
        self.memory_content.setMinimumHeight(110)
        form.addRow("Название", self.memory_title)
        form.addRow("Категория", self.memory_category)
        form.addRow("Теги через запятую", self.memory_tags)
        form.addRow("Содержание", self.memory_content)
        layout.addWidget(form_widget)

        buttons = QHBoxLayout()
        save = QPushButton("Сохранить запись")
        save.clicked.connect(self._save_memory)
        clear = QPushButton("Новая запись")
        clear.clicked.connect(self._clear_memory_form)
        delete = QPushButton("Удалить выбранную")
        delete.clicked.connect(self._delete_selected_memory)
        buttons.addWidget(save)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        buttons.addWidget(delete)
        layout.addLayout(buttons)
        self.tabs.addTab(page, "Долговременная память")

    def _build_search_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        description = QLabel(
            "Проверь, какие документы и записи памяти находятся по запросу. "
            "В этом окне показан полнотекстовый поиск; ответы ассистента дополнительно "
            "могут использовать семантический поиск, если он включён в настройках."
        )
        description.setWordWrap(True)
        description.setObjectName("hint")
        layout.addWidget(description)

        search_row = QHBoxLayout()
        self.search_query = QLineEdit()
        self.search_query.setPlaceholderText("Например: архитектура LocalAssistant, настройки Ollama…")
        self.search_query.returnPressed.connect(self._search_knowledge)
        search_button = QPushButton("Найти")
        search_button.clicked.connect(self._search_knowledge)
        search_row.addWidget(self.search_query, 1)
        search_row.addWidget(search_button)
        layout.addLayout(search_row)

        self.search_results = QListWidget()
        self.search_results.currentItemChanged.connect(self._show_search_result)
        layout.addWidget(self.search_results, 1)
        self.search_preview = QPlainTextEdit()
        self.search_preview.setReadOnly(True)
        self.search_preview.setPlaceholderText("Выберите результат, чтобы посмотреть найденный фрагмент.")
        self.search_preview.setMaximumHeight(190)
        layout.addWidget(self.search_preview)
        self.tabs.addTab(page, "Поиск")

    def _search_knowledge(self) -> None:
        query = self.search_query.text().strip()
        self.search_results.clear()
        self.search_preview.clear()
        if not query:
            self.status.setText("Введите запрос для поиска.")
            return
        try:
            results = self.store.search(query, document_limit=20, memory_limit=20)
        except Exception as exc:
            logger.exception("Не удалось выполнить поиск по локальной базе знаний")
            QMessageBox.warning(self, "Поиск по базе знаний", str(exc))
            return
        for result in results.get("documents", []):
            label = f"Документ · {result.get('name', 'Без названия')} · {result.get('locator', 'фрагмент')}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, {"kind": "document", **result})
            self.search_results.addItem(item)
        for result in results.get("memories", []):
            label = f"Память · {result.get('title', 'Без названия')} · {result.get('category', 'без категории')}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, {"kind": "memory", **result})
            self.search_results.addItem(item)
        self.status.setText(f"Найдено результатов: {self.search_results.count()}.")
        if self.search_results.count() == 0:
            self.search_preview.setPlainText("Совпадений не найдено.")

    def _show_search_result(self, current, _previous=None) -> None:
        if current is None:
            self.search_preview.clear()
            return
        result = current.data(Qt.ItemDataRole.UserRole) or {}
        if result.get("kind") == "document":
            text = (
                f"Источник: {result.get('name', '')}\n"
                f"Место: {result.get('locator', 'фрагмент')}\n"
                f"Путь: {result.get('path', '')}\n\n"
                f"{result.get('content', '')}"
            )
        else:
            tags = result.get("tags", "[]")
            text = (
                f"Память: {result.get('title', '')}\n"
                f"Категория: {result.get('category', '')}\n"
                f"Теги: {tags}\n\n"
                f"{result.get('content', '')}"
            )
        self.search_preview.setPlainText(text)

    def _refresh_documents(self) -> None:
        documents = self.store.list_documents()
        self.documents_table.setRowCount(len(documents))
        for row, document in enumerate(documents):
            name = QTableWidgetItem(str(document["name"]))
            name.setData(Qt.ItemDataRole.UserRole, int(document["id"]))
            name.setToolTip(str(document["path"]))
            self.documents_table.setItem(row, 0, name)
            self.documents_table.setItem(row, 1, QTableWidgetItem(str(document["chunk_count"])))
            self.documents_table.setItem(row, 2, QTableWidgetItem(f"{int(document['size_bytes']) / 1024:.1f} КБ"))
            self.documents_table.setItem(row, 3, QTableWidgetItem(str(document.get("embedding_model") or "Только полнотекстовый")))
        self.documents_table.resizeRowsToContents()

    def _choose_documents(self) -> None:
        dialog_options: dict = {}
        if not file_dialog_uses_native(
            bool(self.settings.get("hide_from_capture", True))
        ):
            # A native Windows dialog is its own OS window and would be
            # visible in a screen share; Qt's own dialog is protected.
            dialog_options["options"] = QFileDialog.DontUseNativeDialog
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Добавить документы в локальную базу знаний",
            str(Path.home()),
            "Документы (*.pdf *.docx *.txt *.md *.markdown)",
            **dialog_options,
        )
        if not paths:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._set_import_running(True)
        self._worker = _DocumentImportWorker(
            paths, self.settings, embedder=self.embedder, parent=self
        )
        self._worker.progress.connect(self.status.setText)
        self._worker.completed.connect(self._on_import_completed)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _set_import_running(self, running: bool) -> None:
        self.add_documents_button.setEnabled(not running)
        self.remove_document_button.setEnabled(not running)
        self.reindex_document_button.setEnabled(not running)
        self.import_progress.setVisible(running)
        self.cancel_import_button.setEnabled(running)

    def _cancel_worker(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            self.cancel_import_button.setEnabled(False)
            self.status.setText("Отменяю операцию после завершения текущего безопасного шага…")

    def _on_worker_finished(self) -> None:
        # QThread has fully returned; only now is it safe to start another job.
        self._set_import_running(False)

    def _on_import_completed(self, results: list[dict]) -> None:
        imported = sum(1 for item in results if item.get("ok") and item.get("status") == "imported")
        unchanged = sum(1 for item in results if item.get("ok") and item.get("status") == "unchanged")
        indexed = sum(1 for item in results if item.get("ok") and item.get("status") == "indexed")
        cancelled = sum(1 for item in results if item.get("ok") and item.get("status") == "cancelled")
        failed = [item for item in results if not item.get("ok")]
        embedding_warnings = [item for item in results if item.get("embedding_warning")]
        operation_warnings = [item for item in results if item.get("warning")]
        try:
            self._refresh_documents()
        except Exception as exc:
            logger.exception("Не удалось обновить список документов после операции")
            QMessageBox.warning(self, "База знаний", f"Операция завершилась, но список документов не обновился:\n{exc}")
        summary = (
            f"Импортировано: {imported}; без изменений: {unchanged}; "
            f"переиндексировано: {indexed}; отменено: {cancelled}; ошибок: {len(failed)}."
        )
        if embedding_warnings:
            summary += f" Ошибки эмбеддингов: {len(embedding_warnings)}; полнотекстовый поиск сохранён."
        self.status.setText(summary)
        if failed:
            details = "\n".join(f"• {item['name']}: {item['error']}" for item in failed[:12])
            QMessageBox.warning(self, "Часть документов не импортирована", details)
        elif embedding_warnings or operation_warnings:
            details = "\n".join(
                f"• {item['name']}: {item.get('embedding_warning') or item.get('warning')}"
                for item in (embedding_warnings + operation_warnings)[:8]
            )
            QMessageBox.information(self, "Операция завершена", summary + "\n\n" + details)

    def _reindex_selected_document(self) -> None:
        row = self.documents_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "База знаний", "Сначала выберите документ.")
            return
        item = self.documents_table.item(row, 0)
        document_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if document_id is None:
            return
        if self.embedder is None:
            QMessageBox.warning(
                self, "База знаний",
                "Не удалось получить общий клиент Ollama. Закройте окно и откройте его из LocalAssistant снова.",
            )
            return
        if not str(self.settings.get("embedding_model", "")).strip():
            QMessageBox.warning(
                self, "Модель эмбеддингов",
                "Сначала выберите модель эмбеддингов в Настройки → Модели.",
            )
            return
        self._set_import_running(True)
        self._worker = _DocumentImportWorker(
            [], self.settings, embedder=self.embedder,
            document_ids=[int(document_id)], parent=self,
        )
        self._worker.progress.connect(self.status.setText)
        self._worker.completed.connect(self._on_import_completed)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _remove_selected_document(self) -> None:
        row = self.documents_table.currentRow()
        if row < 0:
            return
        item = self.documents_table.item(row, 0)
        document_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if document_id is None:
            return
        answer = QMessageBox.question(
            self, "Удалить документ",
            "Удалить документ и все его извлечённые фрагменты из локальной базы знаний? Исходный файл на диске не будет затронут.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.store.remove_document(int(document_id))
        self._refresh_documents()
        self.status.setText("Документ удалён из базы знаний.")

    def _refresh_memories(self) -> None:
        memories = self.store.list_memories()
        self.memories_table.setRowCount(len(memories))
        for row, memory in enumerate(memories):
            title = QTableWidgetItem(str(memory["title"]))
            title.setData(Qt.ItemDataRole.UserRole, int(memory["id"]))
            self.memories_table.setItem(row, 0, title)
            self.memories_table.setItem(row, 1, QTableWidgetItem(str(memory["category"])))
            stamp = time_label(memory["updated_at"])
            self.memories_table.setItem(row, 2, QTableWidgetItem(stamp))
        self.memories_table.resizeRowsToContents()

    def _load_selected_memory(self) -> None:
        row = self.memories_table.currentRow()
        if row < 0:
            return
        item = self.memories_table.item(row, 0)
        if item is None:
            return
        memory_id = int(item.data(Qt.ItemDataRole.UserRole))
        memory = next((entry for entry in self.store.list_memories() if int(entry["id"]) == memory_id), None)
        if memory is None:
            self._clear_memory_form()
            return
        self._selected_memory_id = memory_id
        self.memory_title.setText(str(memory["title"]))
        self.memory_category.setText(str(memory["category"]))
        try:
            import json
            tags = json.loads(memory["tags"])
        except (TypeError, ValueError):
            tags = []
        self.memory_tags.setText(", ".join(str(tag) for tag in tags))
        self.memory_content.setPlainText(str(memory["content"]))

    def _save_memory(self) -> None:
        tags = [item.strip() for item in self.memory_tags.text().split(",") if item.strip()]
        try:
            if self._selected_memory_id is None:
                self.store.add_memory(
                    self.memory_title.text(), self.memory_content.toPlainText(),
                    self.memory_category.text(), tags,
                )
                message = "Запись добавлена в долговременную память."
            else:
                self.store.update_memory(
                    self._selected_memory_id, self.memory_title.text(),
                    self.memory_content.toPlainText(), self.memory_category.text(), tags,
                )
                message = "Запись памяти обновлена."
        except KnowledgeError as exc:
            QMessageBox.warning(self, "Долговременная память", str(exc))
            return
        self._refresh_memories()
        self._clear_memory_form()
        self.status.setText(message)

    def _clear_memory_form(self) -> None:
        self._selected_memory_id = None
        self.memories_table.clearSelection()
        self.memory_title.clear()
        self.memory_category.clear()
        self.memory_tags.clear()
        self.memory_content.clear()

    def _delete_selected_memory(self) -> None:
        if self._selected_memory_id is None:
            return
        answer = QMessageBox.question(
            self, "Удалить запись памяти", "Удалить выбранную запись из долговременной памяти?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.store.delete_memory(self._selected_memory_id)
        self._refresh_memories()
        self._clear_memory_form()
        self.status.setText("Запись памяти удалена.")

    def _worker_is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _refuse_close_while_busy(self) -> bool:
        if not self._worker_is_running():
            return False
        QMessageBox.information(
            self, "Импорт документов",
            "Сначала дождитесь завершения операции или нажмите «Отменить» и дождитесь подтверждения."
        )
        return True

    def hide_for_tray(self) -> bool:
        """Hide a busy dialog without closing or cancelling its worker."""
        if not self._worker_is_running():
            return False
        self._hidden_for_tray = True
        self.hide()
        return True

    def restore_from_tray(self) -> bool:
        """Restore a dialog hidden by the main window's tray action."""
        if not self._hidden_for_tray:
            return False
        self._hidden_for_tray = False
        self.show()
        self.raise_()
        self.activateWindow()
        return True

    def accept(self) -> None:
        if self._refuse_close_while_busy():
            return
        self._hidden_for_tray = False
        super().accept()

    def reject(self) -> None:
        if self._refuse_close_while_busy():
            return
        self._hidden_for_tray = False
        super().reject()

    def closeEvent(self, event) -> None:
        if self._refuse_close_while_busy():
            event.ignore()
            return
        self._hidden_for_tray = False
        super().closeEvent(event)


def time_label(value: float) -> str:
    from datetime import datetime
    try:
        return datetime.fromtimestamp(float(value)).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError, OSError):
        return "—"
