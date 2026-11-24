"""Managed background discovery of installed Ollama models."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from .ollama_client import OllamaClient

logger = logging.getLogger("live_assistant.settings")


class ModelsWorker(QThread):
    """Fetch installed models without tying the thread lifetime to a dialog."""

    loaded = Signal(list)

    def __init__(
        self,
        url: str,
        fetch_models: Callable[[str], list[str]] | None = None,
    ) -> None:
        super().__init__()
        self._url = str(url)
        self._fetch_models = fetch_models or OllamaClient.list_models

    def run(self) -> None:
        try:
            models = self._fetch_models(self._url)
            if not isinstance(models, (list, tuple)):
                raise TypeError("Список моделей Ollama имеет неверный формат.")
            cleaned = sorted({
                str(name).strip() for name in models if str(name).strip()
            })
        except Exception:
            logger.exception("Не удалось получить список моделей Ollama.")
            cleaned = []
        self.loaded.emit(cleaned)


_ACTIVE_MODEL_WORKERS: set[ModelsWorker] = set()


def start_model_discovery(url: str, loaded_callback, finished_callback) -> ModelsWorker:
    """Keep a parentless QThread alive until it has really finished."""
    worker = ModelsWorker(url)
    _ACTIVE_MODEL_WORKERS.add(worker)
    worker.loaded.connect(loaded_callback)
    worker.finished.connect(finished_callback)
    worker.finished.connect(
        lambda worker=worker: _ACTIVE_MODEL_WORKERS.discard(worker)
    )
    worker.finished.connect(worker.deleteLater)
    try:
        worker.start()
    except Exception:
        _ACTIVE_MODEL_WORKERS.discard(worker)
        worker.deleteLater()
        raise
    return worker
