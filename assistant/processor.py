"""Serial speech/text processor with streaming generation and SQLite history."""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

from PySide6.QtCore import QThread, Signal

from .conversation_store import ConversationStore
from .config import SYSTEM_PROMPT
from .coding_mode import build_coding_prompt
from .project_context import build_project_context_if_enabled
from .ollama_client import OllamaClient, OllamaError
from .settings_store import SettingsStore
from .speech_filter import DuplicateFilter

logger = logging.getLogger("live_assistant.processor")


class MessageProcessor(QThread):
    user_message = Signal(str, int)
    assistant_message = Signal(str)
    assistant_chunk = Signal(str)
    answer_metadata = Signal(str, int)
    typing_changed = Signal(bool)
    status_changed = Signal(str)
    conversation_cleared = Signal()
    conversation_loaded = Signal(int, str, object)
    conversations_changed = Signal(object)

    def __init__(
        self,
        settings_store: SettingsStore | None = None,
        conversation_store: ConversationStore | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings_store or SettingsStore()
        self.store = conversation_store or ConversationStore()
        self._queue: queue.Queue[tuple[str, Any] | None] = queue.Queue()
        self._running = True
        self._duplicates = DuplicateFilter()
        self._client = OllamaClient(self.settings)
        self._cancel_lock = threading.Lock()
        self._cancel_event: threading.Event | None = None

        saved_id = self.settings.get("active_conversation_id")
        try:
            saved_id = int(saved_id) if saved_id is not None else None
        except (TypeError, ValueError):
            saved_id = None
        if saved_id is None or not self.store.conversation_exists(saved_id):
            saved_id = self.store.create_conversation()
            self.settings.update({"active_conversation_id": saved_id})
        self._conversation_id = saved_id
        meta = {x["id"]: x for x in self.store.list_conversations()}
        self._conversation_title = meta.get(saved_id, {}).get("title", "Новый диалог")

    @property
    def current_conversation_id(self) -> int:
        return self._conversation_id

    def start_current(self) -> None:
        self._emit_current_conversation()

    def enqueue_text(self, text: str, bypass_filter: bool = False) -> None:
        text = text.strip()
        if text:
            self._queue.put(("MESSAGE", (text, bypass_filter)))

    def clear_conversation(self) -> None:
        self._queue.put(("CLEAR", None))

    def new_conversation(self, title: str = "Новый диалог") -> None:
        self._queue.put(("NEW", title))

    def switch_conversation(self, conversation_id: int) -> None:
        self._queue.put(("SWITCH", int(conversation_id)))

    def rename_current_conversation(self, title: str) -> None:
        self._queue.put(("RENAME", title))

    def delete_current_conversation(self) -> None:
        self._queue.put(("DELETE", None))

    def regenerate_last_answer(self) -> None:
        self._queue.put(("REGENERATE", None))

    def edit_last_user_message(self, text: str) -> None:
        """Compatibility shortcut for editing the last user message."""
        text = text.strip()
        if text:
            self.cancel_current()
            self._queue.put(("EDIT_LAST", text))

    def edit_user_message(self, message_id: int, text: str) -> None:
        """Edit a specific user turn and regenerate the conversation from there."""
        text = text.strip()
        if text:
            self.cancel_current()
            self._queue.put(("EDIT_MESSAGE", (int(message_id), text)))

    def toggle_pin_current_conversation(self) -> None:
        self._queue.put(("PIN", None))

    def cancel_current(self) -> None:
        with self._cancel_lock:
            if self._cancel_event is not None:
                self._cancel_event.set()
        self._client.cancel_active_request()

    def stop(self) -> None:
        self._running = False
        self.cancel_current()
        self._queue.put(None)

    def list_conversations(self, query: str = "") -> list[dict[str, Any]]:
        return self.store.list_conversations(query=query)

    def current_messages(self) -> list[dict[str, Any]]:
        return self.store.get_messages(self._conversation_id)

    def _emit_current_conversation(self) -> None:
        messages = self.store.get_messages(self._conversation_id)
        self.conversation_loaded.emit(
            self._conversation_id,
            self._conversation_title,
            messages,
        )
        self.conversations_changed.emit(self.store.list_conversations())

    def _switch_to(self, conversation_id: int) -> None:
        if not self.store.conversation_exists(conversation_id):
            return
        self._conversation_id = conversation_id
        items = {x["id"]: x for x in self.store.list_conversations()}
        self._conversation_title = items.get(conversation_id, {}).get("title", "Диалог")
        self.settings.update({"active_conversation_id": conversation_id})
        self._emit_current_conversation()
        self.status_changed.emit("Диалог открыт")

    def _emit_saved_conversations(self) -> None:
        self.conversations_changed.emit(self.store.list_conversations())

    def _make_context(self) -> list[dict[str, str]]:
        messages = self.store.context_messages(
            self._conversation_id,
            int(self.settings.get("max_history_messages", 16)),
        )
        latest_user = next(
            (str(item["content"]) for item in reversed(messages) if item["role"] == "user"),
            "",
        )
        system_parts = [
            str(self.settings.get("system_prompt", SYSTEM_PROMPT)),
            build_coding_prompt(
                latest_user,
                str(self.settings.get("assistant_mode", "coding")),
            ),
        ]
        project_root = str(self.settings.get("project_root", "") or "").strip()
        project_context = build_project_context_if_enabled(
            project_root,
            bool(self.settings.get("project_context_enabled", True)),
        )
        if project_context:
            system_parts.append(project_context)
        system_content = "\n\n".join(part for part in system_parts if part)
        return [{"role": "system", "content": system_content}] + messages

    def _run_message(self, text: str, bypass_filter: bool, persist_user: bool = True) -> None:
        if not bypass_filter:
            if self._duplicates.is_duplicate(text):
                logger.info("Повтор голосовой фразы пропущен: %s", text)
                self.status_changed.emit("Повтор фразы пропущен")
                return
            if bool(self.settings.get("filter_background_speech", True)):
                self.status_changed.emit("Анализирую намерение…")
                try:
                    accepted, reason = self._client.should_answer(text)
                    logger.info("Решение фильтра (%s): %s", reason, text)
                except OllamaError as exc:
                    logger.exception("Ошибка классификации неоднозначной фразы.")
                    self.status_changed.emit("Ошибка классификатора")
                    self.assistant_message.emit(f"Ошибка классификатора: {exc}")
                    return
                if not accepted:
                    self.status_changed.emit(f"Фраза пропущена фильтром: {reason}")
                    return

        if persist_user:
            message_id = self.store.add_message(self._conversation_id, "user", text)
            self.user_message.emit(text, message_id)
            self._emit_saved_conversations()

        cancel_event = threading.Event()
        with self._cancel_lock:
            self._cancel_event = cancel_event

        self.typing_changed.emit(True)
        self.status_changed.emit("Генерирую ответ…")
        started = time.monotonic()
        parts: list[str] = []
        model = str(self.settings.get("answer_model", "qwen3-coder:30b"))
        try:
            for chunk in self._client.stream_chat(self._make_context(), cancel_event, model=model):
                if cancel_event.is_set():
                    break
                parts.append(chunk)
                self.assistant_chunk.emit(chunk)

            answer = "".join(parts).strip()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if answer:
                self.store.add_message(
                    self._conversation_id,
                    "assistant",
                    answer,
                    model=model,
                    duration_ms=elapsed_ms,
                )
                self.assistant_message.emit(answer)
                self.answer_metadata.emit(model, elapsed_ms)
                if cancel_event.is_set():
                    self.status_changed.emit("Генерация остановлена")
                else:
                    self.status_changed.emit(f"Ответ готов · {model} · {elapsed_ms / 1000:.1f} с")
            elif cancel_event.is_set():
                self.assistant_message.emit("Генерация остановлена.")
                self.status_changed.emit("Генерация остановлена")
            else:
                raise OllamaError("Поток завершился без текста ответа.")
        except OllamaError as exc:
            logger.exception("Не удалось получить ответ от Ollama.")
            self.assistant_message.emit(f"Ошибка ответа: {exc}")
            self.status_changed.emit("Не удалось получить ответ")
        except Exception as exc:
            logger.exception("Неожиданная ошибка обработки сообщения.")
            self.assistant_message.emit(f"Ошибка обработки: {exc}")
            self.status_changed.emit("Ошибка обработки сообщения")
        finally:
            with self._cancel_lock:
                if self._cancel_event is cancel_event:
                    self._cancel_event = None
            self.typing_changed.emit(False)
            self._emit_saved_conversations()

    def _handle_control(self, command: str, payload: Any) -> None:
        if command == "CLEAR":
            self.store.clear_conversation(self._conversation_id)
            self._duplicates = DuplicateFilter()
            self.conversation_cleared.emit()
            self._emit_saved_conversations()
            return
        if command == "NEW":
            self._conversation_id = self.store.create_conversation(str(payload or "Новый диалог"))
            self._conversation_title = str(payload or "Новый диалог")
            self.settings.update({"active_conversation_id": self._conversation_id})
            self._emit_current_conversation()
            self.status_changed.emit("Создан новый диалог")
            return
        if command == "SWITCH":
            self._switch_to(int(payload))
            return
        if command == "RENAME":
            try:
                self.store.rename_conversation(self._conversation_id, str(payload))
                self._conversation_title = str(payload).strip()
                self._emit_current_conversation()
            except ValueError as exc:
                self.status_changed.emit(str(exc))
            return
        if command == "DELETE":
            self.store.delete_conversation(self._conversation_id)
            rows = self.store.list_conversations()
            if rows:
                self._switch_to(int(rows[0]["id"]))
            else:
                self._conversation_id = self.store.create_conversation()
                self._conversation_title = "Новый диалог"
                self.settings.update({"active_conversation_id": self._conversation_id})
                self._emit_current_conversation()
            self._emit_saved_conversations()
            return
        if command == "PIN":
            items = self.store.list_conversations()
            current = next(
                (item for item in items if int(item["id"]) == self._conversation_id),
                None,
            )
            if current is not None:
                pinned = not bool(current.get("is_pinned", 0))
                self.store.set_pinned(self._conversation_id, pinned)
                self._emit_saved_conversations()
                self.status_changed.emit("Диалог закреплён" if pinned else "Диалог откреплён")
            return
        if command == "EDIT_MESSAGE":
            message_id, text = payload
            text = str(text).strip()
            if not self.store.edit_user_message(
                self._conversation_id, int(message_id), text
            ):
                self.status_changed.emit("Сообщение больше недоступно для редактирования")
                return
            self._duplicates = DuplicateFilter()
            self._emit_current_conversation()
            self._run_message(text, bypass_filter=True, persist_user=False)
            return
        if command == "EDIT_LAST":
            text = str(payload).strip()
            if not self.store.edit_last_user_message(self._conversation_id, text):
                self.status_changed.emit("Нет вопроса для редактирования")
                return
            self._duplicates = DuplicateFilter()
            self._emit_current_conversation()
            self._run_message(text, bypass_filter=True, persist_user=False)
            return
        if command == "REGENERATE":
            text = self.store.last_user_message(self._conversation_id)
            if not text:
                self.status_changed.emit("Нет сообщения для повторной генерации")
                return
            self.store.remove_last_assistant_message(self._conversation_id)
            self._run_message(text, bypass_filter=True, persist_user=False)
            return

    def run(self) -> None:
        self._emit_current_conversation()
        while self._running:
            try:
                item = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is None:
                break
            command, payload = item
            if command == "MESSAGE":
                text, bypass_filter = payload
                self._run_message(text, bypass_filter)
            else:
                self._handle_control(command, payload)
