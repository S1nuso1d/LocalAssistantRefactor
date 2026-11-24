"""Thread-safe processor event queue with bounded pending voice phrases."""
from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Any


class ProcessorEventQueue:
    """Thread-safe FIFO with a bounded number of pending voice events.

    Voice text is stored in the event itself. This keeps each phrase attached
    to its position relative to NEW/SWITCH/CLEAR commands and prevents a stale
    VOICE marker from accidentally consuming a newer phrase after Stop.
    """

    def __init__(self, max_voice_phrases: int) -> None:
        self._items: deque[tuple[str, Any] | None] = deque()
        self._max_voice_phrases = max(1, int(max_voice_phrases))
        self._condition = threading.Condition()

    def put(self, item: tuple[str, Any] | None) -> None:
        with self._condition:
            self._items.append(item)
            self._condition.notify()

    def put_voice(self, text: str) -> str | None:
        """Append a voice phrase and drop the oldest pending voice phrase if full."""
        dropped = None
        with self._condition:
            voice_indexes = [
                index for index, item in enumerate(self._items)
                if item is not None and item[0] == "VOICE"
            ]
            if len(voice_indexes) >= self._max_voice_phrases:
                index = voice_indexes[0]
                old = self._items[index]
                dropped = str(old[1]) if old is not None else None
                del self._items[index]
            self._items.append(("VOICE", text))
            self._condition.notify()
        return dropped

    def clear_voice(self) -> list[str]:
        """Remove queued voice phrases without moving or deleting commands."""
        with self._condition:
            kept: deque[tuple[str, Any] | None] = deque()
            cleared: list[str] = []
            while self._items:
                item = self._items.popleft()
                if item is not None and item[0] == "VOICE":
                    cleared.append(str(item[1]))
                else:
                    kept.append(item)
            self._items = kept
            return cleared

    def snapshot(self) -> list[tuple[str, Any] | None]:
        """Return a stable snapshot, primarily for regression tests."""
        with self._condition:
            return list(self._items)

    def get(self, timeout: float | None = None) -> tuple[str, Any] | None:
        with self._condition:
            if timeout is None:
                while not self._items:
                    self._condition.wait()
            else:
                deadline = time.monotonic() + max(0.0, timeout)
                while not self._items:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty
                    self._condition.wait(remaining)
            return self._items.popleft()

