"""Helpers for graceful shutdown from a console signal."""
from __future__ import annotations

import signal
from collections.abc import Callable
from types import FrameType
from typing import Any


def install_console_interrupt_handler(
    callback: Callable[[], None],
    *,
    schedule: Callable[[Callable[[], None]], None],
    signal_api: Any = signal,
):
    """Route Ctrl+C to a scheduled application shutdown instead of KeyboardInterrupt.

    The callback is scheduled only once even when the user presses Ctrl+C more
    than once while worker threads are still shutting down. The previous
    handler is returned so the caller can restore it after the Qt event loop.
    """
    previous = signal_api.getsignal(signal_api.SIGINT)
    requested = False

    def handle_interrupt(_signum: int, _frame: FrameType | None) -> None:
        nonlocal requested
        if requested:
            return
        requested = True
        schedule(callback)

    signal_api.signal(signal_api.SIGINT, handle_interrupt)
    return previous


def console_attached(stream: Any = None) -> bool:
    """True when stdout is an interactive console (a Ctrl+C is possible).

    A windowed build (pythonw, frozen --windowed) has no console, so nothing
    needs to be polled for signals there.
    """
    if stream is None:
        import sys
        stream = sys.stdout
    try:
        return bool(stream is not None and stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False
