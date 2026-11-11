"""Small pure helpers shared by the UI, the processor and the store."""
from __future__ import annotations

import re
from datetime import datetime

_THINK_BLOCK = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think>(.*)\Z", re.DOTALL | re.IGNORECASE)
_THINK_CLOSE = re.compile(r"</think>", re.IGNORECASE)


def split_thinking(text: str) -> tuple[str, str, bool]:
    """Split reasoning-model output into (thinking, answer, still_thinking).

    Models such as qwen3 / deepseek-r1 wrap their chain of thought in
    ``<think>…</think>``. It must not be shown as a normal answer, and it
    should not be sent back to the model as conversation history.
    """
    if not text:
        return "", "", False

    thoughts = [m.strip() for m in _THINK_BLOCK.findall(text)]
    rest = _THINK_BLOCK.sub("", text)
    still_thinking = False

    opened = _THINK_OPEN.search(rest)
    if opened:
        thoughts.append(opened.group(1).strip())
        rest = rest[: opened.start()]
        still_thinking = True
    else:
        # Some chat templates pre-open the tag, so only "</think>" arrives.
        closing = _THINK_CLOSE.search(rest)
        if closing:
            thoughts.append(rest[: closing.start()].strip())
            rest = rest[closing.end():]

    thinking = "\n\n".join(t for t in thoughts if t)
    return thinking, rest.strip(), still_thinking


def strip_thinking(text: str) -> str:
    return split_thinking(text)[1]


def format_time(timestamp: float | None) -> str:
    if not timestamp:
        return ""
    try:
        return datetime.fromtimestamp(float(timestamp)).strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


def relative_day(timestamp: float, now: datetime | None = None) -> str:
    """Human-friendly date for the history list (Russian UI)."""
    now = now or datetime.now()
    try:
        moment = datetime.fromtimestamp(float(timestamp))
    except (OverflowError, OSError, ValueError):
        return ""
    days = (now.date() - moment.date()).days
    clock = moment.strftime("%H:%M")
    if days == 0:
        return f"Сегодня, {clock}"
    if days == 1:
        return f"Вчера, {clock}"
    if moment.year == now.year:
        return moment.strftime("%d.%m, ") + clock
    return moment.strftime("%d.%m.%Y")


def safe_filename(title: str, fallback: str = "dialog") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", title).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)[:60].strip(" .")
    return cleaned or fallback
