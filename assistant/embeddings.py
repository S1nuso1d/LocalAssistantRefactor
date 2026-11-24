"""Validated Ollama embedding client with bounded, cancellable HTTP calls."""
from __future__ import annotations

import logging
import math
import threading
from typing import Callable, Iterable

import requests

from .settings_store import normalize_ollama_url

logger = logging.getLogger("live_assistant.embeddings")
MAX_EMBEDDING_DIMENSIONS = 65536
MAX_EMBEDDING_INPUT_CHARS = 16000
MAX_EMBEDDING_ITEMS = 64


class EmbeddingError(RuntimeError):
    """Raised when the configured Ollama embedding model cannot be used."""


class EmbeddingCancelled(EmbeddingError):
    """Raised when embedding work is cancelled by the caller."""


def _base_url(chat_url: str) -> str:
    normalized = normalize_ollama_url(chat_url)
    if not normalized:
        raise EmbeddingError("Не задан корректный адрес Ollama.")
    return normalized.rsplit("/api/chat", 1)[0].rstrip("/")


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise EmbeddingCancelled("Создание embeddings отменено.")


def _validate_vectors(value, expected: int) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != expected:
        raise EmbeddingError("Ollama вернула неверное количество embedding-векторов.")
    result: list[list[float]] = []
    dimensions: int | None = None
    for vector in value:
        if not isinstance(vector, list) or not vector:
            raise EmbeddingError("Ollama вернула пустой embedding-вектор.")
        if len(vector) > MAX_EMBEDDING_DIMENSIONS:
            raise EmbeddingError("Размерность embedding превышает безопасный предел.")
        try:
            numbers = [float(x) for x in vector]
        except (TypeError, ValueError, OverflowError) as exc:
            raise EmbeddingError("Embedding содержит нечисловые значения.") from exc
        if not all(math.isfinite(x) for x in numbers):
            raise EmbeddingError("Embedding содержит NaN или бесконечность.")
        if dimensions is None:
            dimensions = len(numbers)
        elif len(numbers) != dimensions:
            raise EmbeddingError("Размерности embedding-векторов не совпадают.")
        # Iterative hypot is stable and avoids a huge positional argument list.
        norm = 0.0
        for number in numbers:
            norm = math.hypot(norm, number)
        if not math.isfinite(norm) or norm <= 1e-12:
            raise EmbeddingError("Ollama вернула нулевой или некорректный embedding-вектор.")
        result.append([x / norm for x in numbers])
    return result


def embed_texts(
    texts: Iterable[str],
    *,
    model: str,
    ollama_url: str,
    timeout: int = 180,
    session=None,
    cancel_event: threading.Event | None = None,
    set_active: Callable[[requests.Response | None], None] | None = None,
    clear_active: Callable[[requests.Response | None], None] | None = None,
) -> list[list[float]]:
    """Generate normalized vectors through /api/embed, with a legacy fallback.

    The caller may supply a shared requests.Session and lifecycle callbacks.
    In LocalAssistant these are provided by OllamaClient so embeddings are
    serialized with chat/classifier/warm-up requests and can be interrupted.
    The legacy /api/embeddings endpoint is used only for HTTP 404 responses.
    """
    items = [str(text).strip() for text in texts]
    if not items:
        return []
    model = str(model).strip()
    if not model:
        raise EmbeddingError("Не выбрана модель эмбеддингов.")
    if any(not item for item in items):
        raise EmbeddingError("Нельзя создавать embedding для пустого текста.")
    if any(len(item) > MAX_EMBEDDING_INPUT_CHARS for item in items):
        raise EmbeddingError(
            f"Один текст для embedding не может превышать {MAX_EMBEDDING_INPUT_CHARS} символов."
        )
    if len(items) > MAX_EMBEDDING_ITEMS:
        raise EmbeddingError(
            f"За один запрос можно обработать не более {MAX_EMBEDDING_ITEMS} фрагментов."
        )

    base = _base_url(ollama_url)
    requester = session or requests
    timeout_value = (5, max(10, min(int(timeout), 600)))

    def request_json(endpoint: str, payload: dict, *, allow_404: bool = False):
        _check_cancel(cancel_event)
        response = None
        try:
            response = requester.post(
                f"{base}{endpoint}",
                json=payload,
                stream=True,
                timeout=timeout_value,
            )
            if set_active:
                set_active(response)
            _check_cancel(cancel_event)
            if allow_404 and response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
            _check_cancel(cancel_event)
            if isinstance(data, dict) and data.get("error"):
                raise EmbeddingError(str(data["error"])[:600])
            return data
        except EmbeddingCancelled:
            raise
        except requests.RequestException as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise EmbeddingCancelled("Создание embeddings отменено.") from exc
            raise EmbeddingError(f"Ошибка Ollama Embeddings API: {exc}") from exc
        except ValueError as exc:
            raise EmbeddingError(f"Ollama вернула некорректный JSON: {exc}") from exc
        finally:
            if response is not None:
                if clear_active:
                    clear_active(response)
                try:
                    response.close()
                except Exception:
                    logger.debug("Embedding response already closed.", exc_info=True)

    try:
        payload = request_json(
            "/api/embed",
            {
                "model": model,
                "input": items,
                "truncate": False,
                "keep_alive": "5m",
            },
            allow_404=True,
        )
        if payload is not None:
            vectors = payload.get("embeddings") if isinstance(payload, dict) else None
            return _validate_vectors(vectors, len(items))

        # Older Ollama releases expose one embedding per request.
        legacy: list[list[float]] = []
        for item in items:
            _check_cancel(cancel_event)
            result = request_json(
                "/api/embeddings",
                {"model": model, "prompt": item},
            )
            vector = result.get("embedding") if isinstance(result, dict) else None
            legacy.append(vector)
        return _validate_vectors(legacy, len(items))
    except EmbeddingError:
        raise
