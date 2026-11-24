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


def stream_interval_ms(char_count: int) -> int:
    """UI refresh period while streaming; Markdown re-parse cost grows with
    the text, so long answers are redrawn less often."""
    if char_count < 1500:
        return 45
    if char_count < 4000:
        return 90
    if char_count < 8000:
        return 160
    return 260


_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([\w+#.\-]*)[^`]*$")


def extract_code_blocks(markdown: str) -> list[tuple[str, str]]:
    """Return [(language, code)] for every *closed* fenced block.

    An unfinished fence (still streaming) is ignored so the UI never offers
    to copy half a snippet.
    """
    blocks: list[tuple[str, str]] = []
    fence: str | None = None
    language = ""
    buffer: list[str] = []
    for line in markdown.splitlines():
        if fence is None:
            match = _FENCE_OPEN.match(line)
            if match:
                fence, language, buffer = match.group(1), match.group(2), []
            continue
        stripped = line.strip()
        if (
            stripped.startswith(fence[0] * len(fence))
            and set(stripped) == {fence[0]}
        ):
            blocks.append((language, "\n".join(buffer)))
            fence = None
        else:
            buffer.append(line)
    return blocks


def estimate_tokens(text: str) -> int:
    """Rough token count (~3 chars/token for Russian/code); deliberately
    on the cautious side so trimming happens before Ollama truncates."""
    return max(1, len(text) // 3 + 4)


def trim_to_budget(
    messages: list[dict[str, str]], budget: int
) -> tuple[list[dict[str, str]], int]:
    """Keep the newest messages that fit *budget* tokens.

    Returns (kept, dropped_count). The newest message is always kept, and a
    dangling assistant reply at the start (its question was dropped) is
    removed so the history begins with a user turn.
    """
    kept: list[dict[str, str]] = []
    used = 0
    for message in reversed(messages):
        cost = estimate_tokens(message["content"])
        if kept and used + cost > budget:
            break
        kept.append(message)
        used += cost
    kept.reverse()
    if len(kept) > 1 and kept[0]["role"] == "assistant":
        kept.pop(0)
    return kept, len(messages) - len(kept)


def format_tokens(count: int) -> str:
    return f"{count / 1000:.1f}k" if count >= 1000 else str(int(count))


def format_answer_status(
    model: str,
    elapsed_seconds: float,
    usage: dict | None,
    num_ctx: int,
    estimated_prompt_tokens: int = 0,
    trimmed: int = 0,
) -> str:
    """Status line after an answer: speed, real context fill, trimming."""
    parts = [f"Ответ готов · {model} · {elapsed_seconds:.1f} с"]
    usage = usage or {}
    tokens = usage.get("eval_count")
    eval_ns = usage.get("eval_duration")
    if tokens and eval_ns:
        parts.append(f"{tokens / (eval_ns / 1e9):.0f} ток/с")
    # Ollama's prompt_eval_count excludes KV-cached tokens, so it can
    # understate the fill; never report less than our own estimate.
    fill = max(int(usage.get("prompt_eval_count") or 0), estimated_prompt_tokens)
    if fill:
        parts.append(f"контекст ≈{format_tokens(fill)}/{format_tokens(num_ctx)}")
    if trimmed:
        parts.append(f"старых сообщений не вошло: {trimmed}")
    return " · ".join(parts)


def context_is_overflowing(usage: dict | None, num_ctx: int) -> bool:
    """True when Ollama itself evaluated almost the whole window."""
    used = int((usage or {}).get("prompt_eval_count") or 0)
    return num_ctx > 0 and used >= 0.95 * num_ctx
