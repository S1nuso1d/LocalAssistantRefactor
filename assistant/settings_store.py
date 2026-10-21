"""Persistent user settings stored locally as JSON."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / "LocalAssistant"
    path.mkdir(parents=True, exist_ok=True)
    return path


DEFAULT_SETTINGS: dict[str, Any] = {
    "answer_model": "qwen3-coder:30b",
    "assistant_mode": "coding",
    "project_root": "",
    "project_context_enabled": False,
    "classifier_model": "qwen3-coder:30b",
    "ollama_url": "http://localhost:11434/api/chat",
    "whisper_model": "base",
    "language": "ru",
    "audio_source": "loopback",
    "audio_device": "По умолчанию",
    "microphone_device": "По умолчанию",
    "activation_mode": "continuous",
    "silence_threshold": 0.008,
    "silence_duration": 0.9,
    "max_phrase_duration": 40.0,
    "speech_output_enabled": False,
    "hotkey": "ctrl+shift+space",
    "show_hotkey": "ctrl+shift+a",
    "background_alpha": 224,
    "background_saturation": 65,
    "compact_mode": False,
    "always_on_top": True,
    "close_to_tray": False,
    "filter_background_speech": True,
    "require_assistant_name": False,
    "assistant_names": (
        "помощник", "ассистент", "чат gpt", "чатгпт", "эй помощник",
    ),
    "max_history_messages": 16,
}


class SettingsStore:
    """Thread-safe JSON settings with atomic writes."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else app_data_dir() / "settings.json"
        self._lock = threading.RLock()
        self._values: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self._load()

    def _load(self) -> None:
        with self._lock:
            try:
                if self.path.exists():
                    loaded = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        self._values.update(loaded)
            except (OSError, json.JSONDecodeError):
                # Keep safe defaults; save will repair the file on next change.
                pass

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._values:
                return self._values[key]
            return default

    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def update(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._values.update(values)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(self._values, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)

    def reset(self) -> None:
        self.update(dict(DEFAULT_SETTINGS))
