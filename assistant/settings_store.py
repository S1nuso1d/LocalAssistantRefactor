"""Persistent user settings stored locally as JSON, with validation."""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("live_assistant.settings")


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / "LocalAssistant"
    path.mkdir(parents=True, exist_ok=True)
    return path


DEFAULT_SYSTEM_PROMPT = """
Ты — локальный русскоязычный ассистент.
Отвечай естественно, понятно и по существу, как в обычном чате.
Используй контекст диалога, не повторяй вопрос без необходимости.
Не утверждай, что выполнил действия на компьютере, если ты их не выполнял.
""".strip()

DEFAULT_ASSISTANT_NAMES = [
    "помощник", "ассистент", "чат gpt", "чатгпт", "эй помощник",
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "answer_model": "qwen3-coder:30b",
    "classifier_model": "qwen3-coder:30b",
    "ollama_url": "http://localhost:11434/api/chat",
    "num_ctx": 8192,
    "temperature": 0.3,
    "keep_alive": "10m",
    "answer_timeout": 180,
    "classifier_timeout": 90,
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "whisper_model": "base",
    "whisper_device": "cpu",
    "language": "ru",
    "audio_device": "По умолчанию",
    "silence_threshold": 0.008,
    "silence_duration": 0.9,
    "hotkey": "ctrl+shift+space",
    "background_alpha": 224,
    "compact_mode": False,
    "always_on_top": True,
    "hide_from_capture": True,
    "filter_background_speech": True,
    "require_assistant_name": False,
    "assistant_names": list(DEFAULT_ASSISTANT_NAMES),
    "max_history_messages": 16,
    "window_geometry": None,
}

# key -> (kind, minimum, maximum, choices)
_RULES: dict[str, tuple[str, Any, Any, tuple | None]] = {
    "num_ctx": ("int", 1024, 131072, None),
    "temperature": ("float", 0.0, 2.0, None),
    "answer_timeout": ("int", 10, 3600, None),
    "classifier_timeout": ("int", 5, 600, None),
    "silence_threshold": ("float", 0.001, 0.05, None),
    "silence_duration": ("float", 0.4, 2.5, None),
    "background_alpha": ("int", 175, 250, None),
    "max_history_messages": ("int", 2, 100, None),
    "whisper_device": ("str", None, None, ("cpu", "cuda", "auto")),
    "whisper_model": ("str", None, None, (
        "tiny", "base", "small", "medium", "large-v3",
    )),
    "language": ("str", None, None, ("ru", "en", "auto")),
    "compact_mode": ("bool", None, None, None),
    "always_on_top": ("bool", None, None, None),
    "hide_from_capture": ("bool", None, None, None),
    "filter_background_speech": ("bool", None, None, None),
    "require_assistant_name": ("bool", None, None, None),
    "assistant_names": ("str_list", None, None, None),
    "window_geometry": ("geometry", None, None, None),
    "answer_model": ("nonempty_str", None, None, None),
    "classifier_model": ("nonempty_str", None, None, None),
    "ollama_url": ("nonempty_str", None, None, None),
    "system_prompt": ("nonempty_str", None, None, None),
    "hotkey": ("nonempty_str", None, None, None),
    "keep_alive": ("nonempty_str", None, None, None),
    "audio_device": ("nonempty_str", None, None, None),
}


def coerce_setting(key: str, value: Any) -> Any:
    """Return a safe value for *key*; fall back to the default when invalid.

    Hand-edited or corrupted settings.json must never crash the application.
    """
    rule = _RULES.get(key)
    default = DEFAULT_SETTINGS.get(key)
    if rule is None:
        return value
    kind, low, high, choices = rule
    try:
        if kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                return value.strip().lower() == "true"
            return default
        if kind in {"int", "float"}:
            if isinstance(value, bool):
                return default
            number = int(value) if kind == "int" else float(value)
            return max(low, min(high, number))
        if kind in {"str", "nonempty_str"}:
            text = str(value).strip() if value is not None else ""
            if kind == "nonempty_str" and not text:
                return default
            if choices and text not in choices:
                return default
            return text
        if kind == "str_list":
            if not isinstance(value, (list, tuple)):
                return list(default)
            items = [str(x).strip() for x in value if str(x).strip()]
            return items or list(default)
        if kind == "geometry":
            if value is None:
                return None
            parts = [int(x) for x in value]
            if len(parts) == 4 and parts[2] >= 200 and parts[3] >= 200:
                return parts
            return None
    except (TypeError, ValueError):
        pass
    return default if not isinstance(default, list) else list(default)


class SettingsStore:
    """Thread-safe JSON settings with validation and atomic writes."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else app_data_dir() / "settings.json"
        self._lock = threading.RLock()
        self._values: dict[str, Any] = {
            key: (list(v) if isinstance(v, list) else v)
            for key, v in DEFAULT_SETTINGS.items()
        }
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # Keep the broken file for inspection instead of silently
                # overwriting the user's data on the next save.
                backup = self.path.with_suffix(".json.bad")
                try:
                    self.path.replace(backup)
                    logger.warning("Повреждённый settings.json сохранён как %s", backup)
                except OSError:
                    pass
                return
            except OSError:
                return
            if isinstance(loaded, dict):
                for key, value in loaded.items():
                    self._values[key] = coerce_setting(key, value)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._values and self._values[key] is not None:
                return self._values[key]
            return default

    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def update(self, values: dict[str, Any]) -> None:
        with self._lock:
            for key, value in values.items():
                self._values[key] = coerce_setting(key, value)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(self._values, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)

    def reset(self) -> None:
        """Restore defaults but keep internal state such as the open dialog."""
        defaults = {
            key: (list(v) if isinstance(v, list) else v)
            for key, v in DEFAULT_SETTINGS.items()
        }
        self.update(defaults)
