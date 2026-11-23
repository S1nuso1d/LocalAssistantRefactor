"""Serial speech/text processor with streaming generation and SQLite history."""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from typing import Any

from PySide6.QtCore import QThread, Signal

from .conversation_store import ConversationStore
from .config import MAX_PENDING_VOICE_PHRASES, SYSTEM_PROMPT
from .ollama_client import OllamaClient, OllamaError
from .privacy import describe_text
from .settings_store import SettingsStore
from .speech_filter import DuplicateFilter
from .text_utils import (
    context_is_overflowing, estimate_tokens, format_answer_status,
    strip_thinking, trim_to_budget,
)

logger = logging.getLogger("live_assistant.processor")


class MessageProcessor(QThread):
    user_message = Signal(str)
    assistant_message = Signal(str)
    assistant_chunk = Signal(str)
    error_message = Signal(str)
    answer_finished = Signal(str, bool)  # text, answered_a_voice_question
    phrase_decision = Signal(str, bool, str)  # text, accepted, reason
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
        # Voice phrases wait here (bounded) instead of in the main queue, so a
        # long answer cannot build up a backlog of stale background questions.
        self._voice_lock = threading.Lock()
        self._voice_texts: deque[str] = deque(maxlen=MAX_PENDING_VOICE_PHRASES)
        self._trimmed = 0
        self._prompt_estimate = 0
        self._overflow_warned_for: int | None = None

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
        if not text:
            return
        if bypass_filter:
            self._queue.put(("MESSAGE", (text, True)))
            return
        with self._voice_lock:
            full = len(self._voice_texts) == self._voice_texts.maxlen
            overflowed = self._voice_texts[0] if full else None
            self._voice_texts.append(text)  # oldest is dropped when full
        if overflowed:
            self.phrase_decision.emit(
                overflowed, False, "устарела: пока шёл ответ, пришли новые фразы"
            )
        self._queue.put(("VOICE", None))

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

    def cancel_current(self) -> None:
        with self._voice_lock:
            cleared = list(self._voice_texts)
            self._voice_texts.clear()
        for text in cleared:
            self.phrase_decision.emit(text, False, "сброшена кнопкой «Стоп»")
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
        context: list[dict[str, str]] = []
        for message in messages:
            content = message["content"]
            if message["role"] == "assistant":
                # Reasoning traces waste context and confuse follow-ups.
                content = strip_thinking(content)
            if content.strip():
                context.append({"role": message["role"], "content": content})
        prompt = str(self.settings.get("system_prompt", SYSTEM_PROMPT))

        # The message-count limit alone cannot protect the window: a few huge
        # pasted messages overflow it and Ollama silently drops the oldest
        # tokens, including the system prompt. Trim by estimated tokens too.
        num_ctx = int(self.settings.get("num_ctx", 8192))
        reserve = max(512, num_ctx // 4)  # room for the answer itself
        budget = max(256, num_ctx - reserve - estimate_tokens(prompt))
        context, self._trimmed = trim_to_budget(context, budget)
        self._prompt_estimate = estimate_tokens(prompt) + sum(
            estimate_tokens(m["content"]) for m in context
        )
        return [{"role": "system", "content": prompt}] + context

    def _run_message(
        self,
        text: str,
        bypass_filter: bool,
        persist_user: bool = True,
        restore_on_failure: dict[str, Any] | None = None,
    ) -> None:
        if not bypass_filter:
            if self._duplicates.is_duplicate(text):
                logger.info(
                    "Повтор голосовой фразы пропущен: %s",
                    describe_text(text, self._log_text()),
                )
                self.phrase_decision.emit(text, False, "повтор недавней фразы")
                self.status_changed.emit("Повтор фразы пропущен")
                return
            if bool(self.settings.get("filter_background_speech", True)):
                self.status_changed.emit("Анализирую намерение…")
                try:
                    recent = self.store.context_messages(self._conversation_id, 2)
                    accepted, reason = self._client.should_answer(text, recent)
                    logger.info(
                        "Решение фильтра (%s): %s",
                        reason, describe_text(text, self._log_text()),
                    )
                except OllamaError as exc:
                    logger.exception("Ошибка классификации неоднозначной фразы.")
                    self.phrase_decision.emit(
                        text, False, f"классификатор недоступен: {exc}"[:200]
                    )
                    self.status_changed.emit(f"Ошибка классификатора: {exc}"[:160])
                    return
                self.phrase_decision.emit(text, accepted, reason)
                if not accepted:
                    self.status_changed.emit(f"Фраза пропущена фильтром: {reason}")
                    return
            else:
                self.phrase_decision.emit(text, True, "фильтр выключен")

        if persist_user:
            self.store.add_message(self._conversation_id, "user", text)
            self.user_message.emit(text)
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
                    self.answer_finished.emit(answer, not bypass_filter)
                    usage = getattr(self._client, "last_usage", None)
                    num_ctx = int(self.settings.get("num_ctx", 8192))
                    self.status_changed.emit(format_answer_status(
                        model, elapsed_ms / 1000, usage, num_ctx,
                        self._prompt_estimate, self._trimmed,
                    ))
                    if (
                        context_is_overflowing(usage, num_ctx)
                        and self._overflow_warned_for != self._conversation_id
                    ):
                        self._overflow_warned_for = self._conversation_id
                        self.error_message.emit(
                            "Контекст почти заполнен: старые реплики и даже "
                            "системный промпт могут не учитываться. Увеличьте "
                            "«Контекст» в Настройки → Модели или начните новый диалог."
                        )
            elif cancel_event.is_set():
                self._restore_answer(restore_on_failure)
                self.status_changed.emit("Генерация остановлена")
            else:
                raise OllamaError("Поток завершился без текста ответа.")
        except OllamaError as exc:
            logger.exception("Не удалось получить ответ от Ollama.")
            self._restore_answer(restore_on_failure)
            self.error_message.emit(f"Ошибка ответа: {exc}")
            self.status_changed.emit("Не удалось получить ответ")
        except Exception as exc:
            logger.exception("Неожиданная ошибка обработки сообщения.")
            self._restore_answer(restore_on_failure)
            self.error_message.emit(f"Ошибка обработки: {exc}")
            self.status_changed.emit("Ошибка обработки сообщения")
        finally:
            with self._cancel_lock:
                if self._cancel_event is cancel_event:
                    self._cancel_event = None
            self.typing_changed.emit(False)
            self._emit_saved_conversations()

    def _restore_answer(self, previous: dict[str, Any] | None) -> None:
        """Put back the old answer if regeneration produced nothing."""
        if not previous:
            return
        self.store.add_message(
            self._conversation_id, "assistant", previous["content"],
            model=previous.get("model"), duration_ms=previous.get("duration_ms"),
        )
        self.assistant_message.emit(previous["content"])

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
        if command == "REGENERATE":
            text = self.store.last_user_message(self._conversation_id)
            if not text:
                self.status_changed.emit("Нет сообщения для повторной генерации")
                return
            previous = self.store.pop_last_assistant_message(self._conversation_id)
            self._run_message(
                text, bypass_filter=True, persist_user=False,
                restore_on_failure=previous,
            )
            return

    def _log_text(self) -> bool:
        return bool(self.settings.get("log_transcripts", False))

    def _startup_check(self) -> None:
        try:
            ok, message = self._client.check_ready()
        except Exception:
            logger.exception("Проверка Ollama завершилась ошибкой.")
            return
        if not ok:
            self.error_message.emit(message)
            self.status_changed.emit("Ollama недоступна")
            return
        self.status_changed.emit(message)
        if bool(self.settings.get("warm_up_model", True)):
            self._start_warm_up()

    def _start_warm_up(self) -> None:
        # Separate daemon thread: loading a big model can take minutes and
        # must neither delay the queue nor block application shutdown.
        def work() -> None:
            self.status_changed.emit("Загружаю модель в память…")
            try:
                self._client.warm_up()
            except OllamaError as exc:
                logger.warning("Прогрев модели не удался: %s", exc)
                self.status_changed.emit(f"Не удалось загрузить модель: {exc}"[:160])
                return
            self.status_changed.emit("Модель загружена и готова")

        threading.Thread(target=work, name="model-warm-up", daemon=True).start()

    def recheck_ollama(self) -> None:
        """Re-run the connectivity/model check without restarting the app."""
        self.status_changed.emit("Проверяю Ollama…")
        threading.Thread(
            target=self._startup_check, name="ollama-check", daemon=True
        ).start()

    def run(self) -> None:
        self._emit_current_conversation()
        self._startup_check()
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
            elif command == "VOICE":
                with self._voice_lock:
                    text = self._voice_texts.popleft() if self._voice_texts else None
                if text:
                    self._run_message(text, False)
            else:
                self._handle_control(command, payload)
