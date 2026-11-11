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
from .settings_store import SettingsStore

logger = logging.getLogger("live_assistant.ollama")


class OllamaError(RuntimeError):
    """Readable Ollama/API error shown in the application UI."""


class OllamaClient:
    def __init__(self, settings_store: SettingsStore | None = None) -> None:
        self.settings = settings_store or SettingsStore()
        self.session = requests.Session()
        self._active_lock = threading.RLock()
        self._active_response: requests.Response | None = None

    def _url(self) -> str:
        return str(self.settings.get("ollama_url", OLLAMA_URL)).rstrip("/")

    def _set_active(self, response: requests.Response | None) -> None:
        with self._active_lock:
            self._active_response = response

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
    def _normalize_model(name: str) -> str:
        name = name.strip()
        return name if ":" in name else name + ":latest"

    def check_ready(self) -> tuple[bool, str]:
        """Cheap start-up probe: is Ollama up and are the models installed?"""
        try:
            response = self.session.get(self.tags_url(self._url()), timeout=3)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError):
            return False, (
                "Ollama не отвечает. Запусти приложение Ollama — "
                "голосовые и текстовые ответы заработают после этого."
            )
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
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "keep_alive": str(self.settings.get("keep_alive", "10m")),
            "options": self._options(temperature),
        }
        response: requests.Response | None = None
        try:
            response = self.session.post(
                self._url(), json=payload, timeout=(5, timeout)
            )
            self._set_active(response)
            response.raise_for_status()
            data = response.json()
            if data.get("error"):
                raise OllamaError(str(data["error"]))
            content = data.get("message", {}).get("content", "")
            if not isinstance(content, str) or not content.strip():
                raise OllamaError("Ollama вернула пустой ответ.")
            return content.strip()
        except requests.Timeout as exc:
            raise OllamaError(f"Истекло время ожидания Ollama ({timeout} с).") from exc
        except requests.ConnectionError as exc:
            raise OllamaError(
                "Не удаётся подключиться к Ollama. Проверь, что она запущена на localhost:11434."
            ) from exc
        except requests.HTTPError as exc:
            raise OllamaError(self._http_error_message(exc, response, model)) from exc
        except requests.RequestException as exc:
            raise OllamaError(f"Ошибка запроса к Ollama: {exc}") from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise OllamaError(f"Некорректный ответ Ollama: {exc}") from exc
        finally:
            self._set_active(None)
            if response is not None:
                response.close()

    def classify_ambiguous(self, text: str) -> bool:
        model = str(self.settings.get("classifier_model", MODEL_NAME))
        prompt = f"""
Определи, нужно ли голосовому ИИ-ассистенту отвечать на фразу.
true — вопрос, просьба, команда или обращение за помощью к ассистенту.
false — обычный разговор людей между собой, реплика из видео, случайный
комментарий, шум или обрывок фразы. Если сомневаешься, выбери false.
Верни только JSON: {{"is_request": true}} или {{"is_request": false}}.
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
            raise OllamaError(
                f"Классификатор вернул невалидный JSON: {result[:300]}"
            )
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise OllamaError(
            f"В ответе классификатора нет корректного is_request: {result[:300]}"
        )

    def should_answer(self, text: str) -> tuple[bool, str]:
        result = classify_by_rules(
            text,
            assistant_names=self.settings.get("assistant_names"),
            require_assistant_name=bool(self.settings.get("require_assistant_name", False)),
        )
        if result == "accept":
            return True, "принято правилами"
        if result == "reject":
            return False, "отсеяно правилами"
        if self.classify_ambiguous(text):
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
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            "keep_alive": str(self.settings.get("keep_alive", "10m")),
            "options": self._options(),
        }
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
                    logger.warning("Skipping non-JSON Ollama stream line: %r", raw_line[:200])
                    continue
                if event.get("error"):
                    raise OllamaError(str(event["error"]))
                message = event.get("message", {})
                chunk = message.get("content", "") if isinstance(message, dict) else ""
                if chunk:
                    received = True
                    yield str(chunk)
                if event.get("done"):
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
            self._set_active(None)
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
