"""Safe waiting helpers for application-owned worker threads."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger("live_assistant.lifecycle")


def join_thread_with_warning(
    thread: threading.Thread,
    *,
    warning_after: float,
    poll_interval: float,
    on_slow: Callable[[str, float], None] | None = None,
) -> bool:
    """Wait until a thread exits, reporting once when shutdown takes too long.

    This helper deliberately does not detach, daemonize, or forcibly terminate
    a worker. Some native audio/ML calls cannot be interrupted safely from
    another Python thread; keeping the owner alive is safer than returning
    while that worker can still access it.
    """
    if warning_after < 0:
        raise ValueError("warning_after must be non-negative")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")

    started_at = time.monotonic()
    warned = False
    while thread.is_alive():
        thread.join(timeout=poll_interval)
        elapsed = max(0.0, time.monotonic() - started_at)
        if not warned and elapsed >= warning_after and thread.is_alive():
            warned = True
            if on_slow is not None:
                try:
                    on_slow(thread.name, elapsed)
                except Exception:
                    # Diagnostics must never abort the actual join.
                    logger.exception("Не удалось сообщить о задержке остановки потока.")
    return warned
