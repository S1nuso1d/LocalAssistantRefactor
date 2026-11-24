"""Ollama HTTP client supporting intent classification and cancellable streaming."""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Iterator

import requests

from .config import (
    ANSWER_TIMEOUT,
    CLASSIFIER_TIMEOUT,
    MODEL_NAME,
    OLLAMA_URL,
    SYSTEM_PROMPT,
)
from .speech_filter import classify_by_rules
from .text_utils import strip_thinking
from .settings_store import SettingsStore

logger = logging.getLogger("live_assistant.ollama")


class OllamaError(RuntimeError):
    """Readable Ollama/API error shown in the application UI."""


class OllamaCancelled(OllamaError):
    """An Ollama operation was explicitly cancelled by the user/application."""


class OllamaClient:
    def __init__(self, settings_store: SettingsStore | None = None) -> None:
        self.settings = settings_store or SettingsStore()
        self.session = requests.Session()
        self.last_usage: dict | None = None
        # The same Session and the same Ollama model runner must not receive
        # overlapping lifecycle requests from startup warm-up, health checks,
        # classification and chat generation.  Cancellation closes the active
        # response without taking this lock, allowing the owner to unwind.
        self._request_lock = threading.RLock()
        self._active_lock = threading.RLock()
        self._active_response: requests.Response | None = None

    def _url(self) -> str:
        return str(self.settings.get("ollama_url", OLLAMA_URL)).rstrip("/")

    def _set_active(self, response: requests.Response | None) -> None:
        with self._active_lock:
            self._active_response = response

    def _clear_active(self, response: requests.Response | None) -> None:
        with self._active_lock:
            if self._active_response is response:
                self._active_response = None

    def cancel_active_request(self) -> None:
        """Best-effort close of the active HTTP response to stop generation."""
        with self._active_lock:
            response = self._active_response
        if response is not None:
            try:
                response.close()
            except Exception:
                logger.debug("Response already closed while cancelling.", exc_info=True)

    @staticmethod
    def _extract_error(response: requests.Response) -> str:
        """Prefer Ollama's JSON {"error": ...} over a raw body dump."""
        try:
            try:
                data = response.json()
                if isinstance(data, dict) and data.get("error"):
                    return str(data["error"])[:600].strip()
            except ValueError:
                pass
            return response.text[:600].strip()
        except Exception:
            return ""

    def _http_error_message(
        self, exc: requests.HTTPError, response: requests.Response | None, model: str,
    ) -> str:
        detail = self._extract_error(response) if response is not None else ""
        status = getattr(response, "status_code", None)
        if status == 404 or "not found" in detail.lower():
            return (
                f"Модель «{model}» не найдена в Ollama. "
                f"Скачай её командой: ollama pull {model}"
            )
        return f"HTTP-ошибка Ollama: {exc}. {detail}".strip()

    def _options(self, temperature: float | None = None) -> dict:
        value = self.settings.get("temperature", 0.3) if temperature is None else temperature
        return {
            "temperature": float(value),
            "num_ctx": int(self.settings.get("num_ctx", 8192)),
        }

    @staticmethod
    def tags_url(chat_url: str | None = None) -> str:
        base = (chat_url or OLLAMA_URL).rstrip("/")
        return base.rsplit("/api/chat", 1)[0].rstrip("/") + "/api/tags"

    @staticmethod
    def generate_url(chat_url: str | None = None) -> str:
        base = (chat_url or OLLAMA_URL).rstrip("/")
        return base.rsplit("/api/chat", 1)[0].rstrip("/") + "/api/generate"

    def embed_texts(
        self,
        texts: list[str],
        *,
        model: str,
        timeout: int = 180,
        cancel_event: threading.Event | None = None,
    ) -> list[list[float]]:
        """Create embeddings through the same serialized Ollama session.

        Embedding requests share the model server and HTTP session with chat,
        classifier and warm-up calls. They therefore use the same request lock
        and active-response cancellation mechanism.
        """
        from .embeddings import EmbeddingCancelled, embed_texts

        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise OllamaCancelled("Создание embeddings отменено.")
            if self._request_lock.acquire(timeout=0.1):
                break

        watcher_stop = threading.Event()
        watcher: threading.Thread | None = None
        try:
            if cancel_event is not None:
                # This watcher starts only after this operation owns the shared
                # request lock. Therefore the active response, if any, belongs
                # to this embedding call; it cannot accidentally cancel chat.
                def close_embedding_on_cancel() -> None:
                    while not watcher_stop.wait(0.05):
                        if cancel_event.is_set():
                            self.cancel_active_request()
                            return

                watcher = threading.Thread(
                    target=close_embedding_on_cancel,
                    name="OllamaEmbeddingCancel",
                    daemon=True,
                )
                watcher.start()
            if cancel_event is not None and cancel_event.is_set():
                raise OllamaCancelled("Создание embeddings отменено.")
            try:
                return embed_texts(
                    texts,
                    model=model,
                    ollama_url=self._url(),
                    timeout=timeout,
                    session=self.session,
                    cancel_event=cancel_event,
                    set_active=self._set_active,
                    clear_active=self._clear_active,
                )
            except EmbeddingCancelled as exc:
                raise OllamaCancelled(str(exc)) from exc
        finally:
            watcher_stop.set()
            if watcher is not None and watcher is not threading.current_thread():
                watcher.join(timeout=0.25)
            self._request_lock.release()

    def warm_up(self, cancel_event: threading.Event | None = None) -> None:
        """Load the answer model into memory (no tokens are generated).

        The first request otherwise pays the whole model-load time, which for
        a 30B model can be tens of seconds.
        """
        model = str(self.settings.get("answer_model", MODEL_NAME))
        payload = {
            "model": model,
            "prompt": "",
            "stream": False,
            "keep_alive": str(self.settings.get("keep_alive", "10m")),
        }
        with self._request_lock:
            if cancel_event is not None and cancel_event.is_set():
                raise OllamaCancelled("Прогрев модели отменён.")
            response = None
            try:
                # Streaming mode returns after response headers, not after the
                # complete inference.  This lets Stop close the response while
                # Ollama is still loading the model instead of waiting 300 s.
                response = self.session.post(
                    self.generate_url(self._url()),
                    json=payload,
                    stream=True,
                    timeout=(5, 300),
                )
                self._set_active(response)
                if cancel_event is not None and cancel_event.is_set():
                    raise OllamaCancelled("Прогрев модели отменён.")
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and data.get("error"):
                    raise OllamaError(str(data["error"]))
                if cancel_event is not None and cancel_event.is_set():
                    raise OllamaCancelled("Прогрев модели отменён.")
            except OllamaCancelled:
                raise
            except requests.HTTPError as exc:
                if cancel_event is not None and cancel_event.is_set():
                    raise OllamaCancelled("Прогрев модели отменён.") from exc
                raise OllamaError(self._http_error_message(exc, response, model)) from exc
            except requests.RequestException as exc:
                if cancel_event is not None and cancel_event.is_set():
                    raise OllamaCancelled("Прогрев модели отменён.") from exc
                raise OllamaError(f"Не удалось загрузить модель: {exc}") from exc
            finally:
                self._clear_active(response)
                if response is not None:
                    response.close()

    @staticmethod
    def _normalize_model(name: str) -> str:
        name = name.strip()
        return name if ":" in name else name + ":latest"

    def check_ready(self) -> tuple[bool, str]:
        """Cheap start-up probe: is Ollama up and are the models installed?"""
        with self._request_lock:
            response = None
            try:
                response = self.session.get(self.tags_url(self._url()), timeout=3)
                self._set_active(response)
                response.raise_for_status()
                data = response.json()
            except (requests.RequestException, ValueError):
                return False, (
                    "Ollama не отвечает. Запусти приложение Ollama — "
                    "голосовые и текстовые ответы заработают после этого."
                )
            finally:
                self._clear_active(response)
                if response is not None:
                    response.close()
        installed = {
            self._normalize_model(str(item.get("name", "")))
            for item in data.get("models", []) if item.get("name")
        }
        wanted = {
            str(self.settings.get("answer_model", MODEL_NAME)),
            str(self.settings.get("classifier_model", MODEL_NAME)),
        }
        missing = sorted(m for m in wanted if self._normalize_model(m) not in installed)
        if missing:
            return False, (
                "В Ollama не найдены модели: " + ", ".join(missing)
                + ". Установи их (ollama pull …) или выбери другие в настройках."
            )
        return True, "Ollama готова"

    def _post_json(
        self,
        messages: list[dict[str, str]],
        timeout: int,
        temperature: float,
        model: str,
        cancel_event: threading.Event | None = None,
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "keep_alive": str(self.settings.get("keep_alive", "10m")),
            "options": self._options(temperature),
        }
        with self._request_lock:
            if cancel_event is not None and cancel_event.is_set():
                raise OllamaCancelled("Запрос к классификатору отменён.")
            response: requests.Response | None = None
            try:
                response = self.session.post(
                    self._url(), json=payload, stream=True, timeout=(5, timeout)
                )
                self._set_active(response)
                if cancel_event is not None and cancel_event.is_set():
                    raise OllamaCancelled("Запрос к классификатору отменён.")
                response.raise_for_status()
                data = response.json()
                if cancel_event is not None and cancel_event.is_set():
                    raise OllamaCancelled("Запрос к классификатору отменён.")
                if data.get("error"):
                    raise OllamaError(str(data["error"]))
                content = data.get("message", {}).get("content", "")
                if not isinstance(content, str) or not content.strip():
                    raise OllamaError("Ollama вернула пустой ответ.")
                return content.strip()
            except OllamaCancelled:
                raise
            except requests.Timeout as exc:
                if cancel_event is not None and cancel_event.is_set():
                    raise OllamaCancelled("Запрос к классификатору отменён.") from exc
                raise OllamaError(f"Истекло время ожидания Ollama ({timeout} с).") from exc
            except requests.ConnectionError as exc:
                if cancel_event is not None and cancel_event.is_set():
                    raise OllamaCancelled("Запрос к классификатору отменён.") from exc
                raise OllamaError(
                    "Не удаётся подключиться к Ollama. Проверь, что она запущена на localhost:11434."
                ) from exc
            except requests.HTTPError as exc:
                if cancel_event is not None and cancel_event.is_set():
                    raise OllamaCancelled("Запрос к классификатору отменён.") from exc
                raise OllamaError(self._http_error_message(exc, response, model)) from exc
            except requests.RequestException as exc:
                if cancel_event is not None and cancel_event.is_set():
                    raise OllamaCancelled("Запрос к классификатору отменён.") from exc
                raise OllamaError(f"Ошибка запроса к Ollama: {exc}") from exc
            except (ValueError, TypeError, KeyError) as exc:
                if cancel_event is not None and cancel_event.is_set():
                    raise OllamaCancelled("Запрос к классификатору отменён.") from exc
                raise OllamaError(f"Некорректный ответ Ollama: {exc}") from exc
            finally:
                self._clear_active(response)
                if response is not None:
                    response.close()

    @staticmethod
    def format_dialogue_context(context: list[dict[str, str]] | None) -> str:
        """Compact transcript of the latest turns for the classifier prompt."""
        lines = []
        for message in (context or [])[-2:]:
            who = "Пользователь" if message.get("role") == "user" else "Ассистент"
            content = strip_thinking(str(message.get("content", ""))).replace("\n", " ")
            if content.strip():
                lines.append(f"{who}: {content.strip()[:200]}")
        return "\n".join(lines)

    def classify_ambiguous(
        self,
        text: str,
        context: list[dict[str, str]] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> bool:
        model = str(self.settings.get("classifier_model", MODEL_NAME))
        dialogue = self.format_dialogue_context(context)
        context_block = (
            f"Последние реплики диалога с ассистентом:\n{dialogue}\n"
            "Если фраза выглядит как продолжение или уточнение этого диалога, "
            "выбери true.\n"
            if dialogue else ""
        )
        prompt = f"""
Определи, нужно ли голосовому ИИ-ассистенту отвечать на фразу.
true — вопрос, просьба, команда или обращение за помощью к ассистенту.
false — обычный разговор людей между собой, реплика из видео, случайный
комментарий, шум или обрывок фразы. Если сомневаешься, выбери false.
{context_block}Верни только JSON: {{"is_request": true}} или {{"is_request": false}}.
Фраза: {text}
""".strip()
        result = self._post_json(
            [
                {"role": "system", "content": "Ты классификатор. Верни только JSON."},
                {"role": "user", "content": prompt},
            ],
            timeout=int(self.settings.get("classifier_timeout", CLASSIFIER_TIMEOUT)),
            temperature=0.0,
            model=model,
            cancel_event=cancel_event,
        )
        cleaned = re.sub(r"^```(?:json)?\s*", "", result.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        value = None
        try:
            parsed = json.loads(cleaned)
            value = parsed.get("is_request") if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, AttributeError, TypeError):
            # Some local models add a short preface or thinking text before JSON.
            # Recover just the first JSON object that contains is_request.
            decoder = json.JSONDecoder()
            for match in re.finditer(r"\{", cleaned):
                try:
                    parsed, _end = decoder.raw_decode(cleaned[match.start():])
                    if isinstance(parsed, dict) and "is_request" in parsed:
                        value = parsed["is_request"]
                        break
                except json.JSONDecodeError:
                    continue
        if value is None:
            # The model response may echo the recognised utterance. Do not
            # copy it into logs or the UI when transcript logging is disabled.
            raise OllamaError("Классификатор вернул невалидный JSON (содержимое ответа скрыто).")
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise OllamaError(
            "В ответе классификатора нет корректного is_request "
            "(содержимое ответа скрыто)."
        )

    def should_answer(
        self,
        text: str,
        context: list[dict[str, str]] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[bool, str]:
        result = classify_by_rules(
            text,
            assistant_names=self.settings.get("assistant_names"),
            require_assistant_name=bool(self.settings.get("require_assistant_name", False)),
            mode=str(self.settings.get("filter_mode", "balanced")),
        )
        if result == "accept":
            return True, "принято правилами"
        if result == "reject":
            return False, "отсеяно правилами"
        if cancel_event is not None and cancel_event.is_set():
            raise OllamaCancelled("Анализ голосового намерения отменён.")
        if self.classify_ambiguous(text, context, cancel_event=cancel_event):
            return True, "принято локальной моделью"
        return False, "отсеяно локальной моделью"

    def stream_chat(
        self,
        messages: list[dict[str, str]],
        cancel_event: threading.Event,
        model: str | None = None,
    ) -> Iterator[str]:
        """Yield text tokens as Ollama emits NDJSON chunks."""
        model_name = model or str(self.settings.get("answer_model", MODEL_NAME))
        timeout = int(self.settings.get("answer_timeout", ANSWER_TIMEOUT))
        self.last_usage = None
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            "keep_alive": str(self.settings.get("keep_alive", "10m")),
            "options": self._options(),
        }
        with self._request_lock:
            response: requests.Response | None = None
            received = False
            if cancel_event.is_set():
                return
            try:
                response = self.session.post(
                    self._url(), json=payload, stream=True, timeout=(5, timeout)
                )
                self._set_active(response)
                response.raise_for_status()

                for raw_line in response.iter_lines(decode_unicode=True):
                    if cancel_event.is_set():
                        break
                    if not raw_line:
                        continue
                    if isinstance(raw_line, bytes):
                        raw_line = raw_line.decode("utf-8", errors="replace")
                    try:
                        event = json.loads(raw_line)
                    except json.JSONDecodeError:
                        # A malformed server line can contain echoed prompts
                        # or generated text. Log only its size, never content.
                        logger.warning(
                            "Skipping non-JSON Ollama stream line (%d characters).",
                            len(raw_line),
                        )
                        continue
                    if event.get("error"):
                        raise OllamaError(str(event["error"]))
                    message = event.get("message", {})
                    chunk = message.get("content", "") if isinstance(message, dict) else ""
                    if chunk:
                        received = True
                        yield str(chunk)
                    if event.get("done"):
                        self.last_usage = {
                            key: event[key]
                            for key in ("prompt_eval_count", "eval_count", "eval_duration")
                            if isinstance(event.get(key), (int, float))
                        }
                        break
                if not cancel_event.is_set() and not received:
                    raise OllamaError("Ollama завершила поток без текста ответа.")
            except requests.Timeout as exc:
                if not cancel_event.is_set():
                    raise OllamaError(f"Ожидание ответа Ollama превысило {timeout} с.") from exc
            except requests.ConnectionError as exc:
                if not cancel_event.is_set():
                    raise OllamaError("Не удаётся подключиться к Ollama.") from exc
            except requests.HTTPError as exc:
                if not cancel_event.is_set():
                    raise OllamaError(self._http_error_message(exc, response, model_name)) from exc
            except requests.RequestException as exc:
                if not cancel_event.is_set():
                    raise OllamaError(f"Ошибка потокового ответа Ollama: {exc}") from exc
            finally:
                self._clear_active(response)
                if response is not None:
                    response.close()

    @staticmethod
    def list_models(ollama_url: str | None = None) -> list[str]:
        """Query locally installed models, without failing the settings dialog if offline."""
        url = OllamaClient.tags_url(ollama_url)
        try:
            response = requests.get(url, timeout=2)
            response.raise_for_status()
            data = response.json()
            names = [item.get("name", "") for item in data.get("models", [])]
            return sorted(name for name in names if name)
        except (requests.RequestException, ValueError, TypeError):
            logger.info("Could not discover Ollama models; using configured defaults.")
            return []
