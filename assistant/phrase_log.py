"""In-memory journal of what happened to every recognised voice phrase.

It answers the question "why didn't the assistant react?" and makes the
filter tunable on real audio. Qt-free, so it is unit-tested.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    timestamp: float
    text: str
    accepted: bool
    reason: str


class PhraseLog:
    def __init__(self, maxlen: int = 60) -> None:
        self._items: deque[Decision] = deque(maxlen=max(1, maxlen))
        self._lock = threading.Lock()

    def add(self, text: str, accepted: bool, reason: str, timestamp: float | None = None) -> Decision:
        decision = Decision(
            timestamp if timestamp is not None else time.time(),
            text.strip(), bool(accepted), reason.strip(),
        )
        with self._lock:
            self._items.append(decision)
        return decision

    def items(self) -> list[Decision]:
        """Newest first."""
        with self._lock:
            return list(reversed(self._items))

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def stats(self) -> tuple[int, int]:
        """(accepted, skipped)."""
        with self._lock:
            accepted = sum(1 for d in self._items if d.accepted)
            return accepted, len(self._items) - accepted
