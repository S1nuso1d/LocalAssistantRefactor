"""Privacy-conscious application diagnostics; no Qt dependency."""
from __future__ import annotations

import platform
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests


def display_endpoint(value: str) -> str:
    """Return an endpoint suitable for a diagnostic report, without credentials."""
    raw = str(value or "").strip()
    if not raw:
        return "не задан"
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urlsplit(raw)
        host = parts.hostname or "неизвестный сервер"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parts.port:
            host = f"{host}:{parts.port}"
        # Credentials, query strings and fragments may contain secrets.
        # Host is sufficient to identify the target server. Custom reverse-proxy
        # paths can themselves contain tenant IDs or access tokens, so omit them.
        return urlunsplit((parts.scheme, host, "", "", ""))
    except ValueError:
        return "некорректный адрес (значение скрыто)"


def human_size(size: int | float | None) -> str:
    value = max(0, int(size or 0))
    units = ("Б", "КБ", "МБ", "ГБ", "ТБ")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "Б" else f"{int(amount)} Б"
        amount /= 1024
    return f"{value} Б"


def inspect_sqlite(path: str | Path) -> dict[str, Any]:
    """Inspect an existing SQLite DB read-only; never create or migrate it."""
    db_path = Path(path)
    if not db_path.exists():
        return {"status": "not-created", "detail": "Файл базы ещё не создан", "size": 0}
    try:
        size = db_path.stat().st_size
        uri = db_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            result = connection.execute("PRAGMA quick_check").fetchone()
            check = str(result[0]) if result else "нет результата"
            if check.lower() != "ok":
                return {"status": "error", "detail": f"SQLite quick_check: {check[:240]}", "size": size}
            tables = int(connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0])
            return {
                "status": "ok",
                "detail": f"Проверка целостности пройдена · таблиц: {tables}",
                "size": size,
            }
        finally:
            connection.close()
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {"status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:180]}", "size": 0}


def _ollama_tags_url(chat_url: str) -> str:
    base = str(chat_url or "http://localhost:11434/api/chat").rstrip("/")
    if base.endswith("/api/chat"):
        base = base[:-len("/api/chat")]
    return base + "/api/tags"


def probe_ollama(settings: dict[str, Any], timeout: tuple[float, float] = (2.0, 4.0)) -> dict[str, Any]:
    """Query Ollama's tags endpoint with a short timeout and no model loading."""
    endpoint = str(settings.get("ollama_url", "http://localhost:11434/api/chat"))
    started = time.monotonic()
    try:
        with requests.get(_ollama_tags_url(endpoint), timeout=timeout) as response:
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Ollama вернула неожиданный формат /api/tags")
        models = sorted(
            str(item.get("name", "")) for item in payload.get("models", [])
            if isinstance(item, dict) and item.get("name")
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        configured = {
            "ответы": str(settings.get("answer_model", "")),
            "классификатор": str(settings.get("classifier_model", "")),
        }
        embedding = str(settings.get("embedding_model", "")).strip()
        if embedding:
            configured["embeddings"] = embedding
        normalized = {name if ":" in name else name + ":latest" for name in models}
        roles = {
            role: ("установлена" if (name if ":" in name else name + ":latest") in normalized else "не найдена")
            for role, name in configured.items() if name
        }
        return {
            "status": "ok", "detail": f"Доступна · {elapsed_ms} мс · моделей: {len(models)}",
            "models": models, "roles": roles, "elapsed_ms": elapsed_ms,
            "endpoint": display_endpoint(endpoint),
        }
    except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
        return {
            "status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:180]}",
            "models": [], "roles": {}, "endpoint": display_endpoint(endpoint),
        }



def probe_nvidia_gpu(timeout: float = 2.5) -> dict[str, str]:
    """Read basic NVIDIA telemetry when nvidia-smi is available; never fail startup."""
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "check": False,
    }
    if platform.system() == "Windows":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            **kwargs,
        )
        if result.returncode != 0:
            return {"status": "info", "detail": "nvidia-smi недоступен или драйвер не отвечает"}
        first = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
        parts = [part.strip() for part in first.split(",")]
        if len(parts) < 4:
            return {"status": "warning", "detail": "Не удалось разобрать ответ nvidia-smi"}
        return {
            "status": "ok",
            "detail": f"{parts[0]} · VRAM {parts[1]}/{parts[2]} МиБ · GPU {parts[3]}%",
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        label = "nvidia-smi не найден" if isinstance(exc, OSError) else "тайм-аут nvidia-smi"
        return {"status": "info", "detail": label}

def collect_diagnostics(
    settings: dict[str, Any],
    audio_status: dict[str, str],
    listening: bool,
    speaking: bool,
    conversation_db: str | Path,
    knowledge_db: str | Path,
    app_data: str | Path,
) -> dict[str, Any]:
    """Collect a compact support snapshot without reading transcripts or log contents."""
    app_dir = Path(app_data)
    conv = inspect_sqlite(conversation_db)
    knowledge = inspect_sqlite(knowledge_db)
    ollama = probe_ollama(settings)
    gpu = probe_nvidia_gpu()
    try:
        disk = shutil.disk_usage(app_dir)
        disk_status = "warning" if disk.free < 1024 ** 3 else "ok"
        disk_detail = f"Свободно {human_size(disk.free)} из {human_size(disk.total)}"
    except OSError as exc:
        disk_status = "info"
        disk_detail = f"Не удалось получить сведения: {type(exc).__name__}"

    rows: list[dict[str, str]] = [
        {"group": "Приложение", "name": "Python", "status": "ok", "detail": platform.python_version()},
        {"group": "Приложение", "name": "ОС", "status": "info", "detail": f"{platform.system()} {platform.release()} · {platform.machine()}"},
        {"group": "Приложение", "name": "Архитектура", "status": "info", "detail": platform.architecture()[0]},
        {"group": "Ollama", "name": "Сервер", "status": ollama["status"], "detail": f"{ollama['endpoint']} · {ollama['detail']}"},
        {"group": "Оборудование", "name": "NVIDIA GPU", "status": gpu["status"], "detail": gpu["detail"]},
        {"group": "Хранилище", "name": "Свободное место", "status": disk_status, "detail": disk_detail},
    ]
    for role, state in ollama.get("roles", {}).items():
        rows.append({"group": "Ollama", "name": f"Модель: {role}", "status": "ok" if state == "установлена" else "warning", "detail": state})

    enabled_sources = {
        "system": bool(settings.get("system_audio_enabled", True)),
        "microphone": bool(settings.get("microphone_enabled", False)),
    }
    labels = {"system": "Системный звук", "microphone": "Микрофон"}
    for source_id, label in labels.items():
        if not enabled_sources[source_id]:
            state, detail = "info", "Отключён в настройках"
        else:
            detail = str(audio_status.get(source_id, "Состояние ещё не получено"))
            if detail.startswith(("Недоступен", "Ошибка")):
                state = "error"
            elif detail.startswith(("Ожидает", "Остановлен", "Состояние ещё")):
                state = "warning"
            else:
                state = "ok"
        rows.append({"group": "Аудио", "name": label, "status": state, "detail": detail})
    rows.extend([
        {"group": "Аудио", "name": "Прослушивание", "status": "ok" if listening else "warning", "detail": "Активно" if listening else "Приостановлено"},
        {"group": "Аудио", "name": "Озвучка", "status": "info" if not speaking else "ok", "detail": "Сейчас говорит" if speaking else str(settings.get("tts_mode", "off"))},
        {"group": "Whisper", "name": "Модель", "status": "info", "detail": str(settings.get("whisper_model", "не задана"))},
        {"group": "Whisper", "name": "Устройство", "status": "info", "detail": str(settings.get("whisper_device", "cpu"))},
        {"group": "База данных", "name": "История диалогов", "status": conv["status"], "detail": f"{conv['detail']} · {human_size(conv['size'])}"},
        {"group": "База данных", "name": "База знаний", "status": knowledge["status"], "detail": f"{knowledge['detail']} · {human_size(knowledge['size'])}"},
    ])
    log_path = app_dir / "live_assistant.log"
    try:
        log_size = log_path.stat().st_size if log_path.exists() else 0
    except OSError:
        log_size = 0
    rows.append({"group": "Хранилище", "name": "Технический журнал", "status": "info", "detail": f"{human_size(log_size)} · содержимое не читается"})

    return {
        "rows": rows,
        "models": ollama.get("models", []),
        "summary": {
            "ollama_ok": ollama["status"] == "ok",
            "audio_enabled": any(enabled_sources.values()),
            "database_errors": sum(item["status"] == "error" for item in (conv, knowledge)),
        },
    }


def format_diagnostic_report(report: dict[str, Any], build_version: str) -> str:
    """Format a shareable report; deliberately excludes chat and transcript contents."""
    lines = [f"LocalAssistant {build_version}", "Диагностический отчёт (без текста диалогов и распознанной речи)", ""]
    for row in report.get("rows", []):
        lines.append(f"[{row.get('group', '')}] {row.get('name', '')}: {row.get('status', '')} — {row.get('detail', '')}")
    models = report.get("models") or []
    lines.extend(["", "Установленные модели Ollama:"])
    lines.extend(f"- {model}" for model in models)
    if not models:
        lines.append("- список недоступен или пуст")
    return "\n".join(lines)
