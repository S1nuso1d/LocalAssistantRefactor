"""Audio-device change detection and bounded retry scheduling."""
from __future__ import annotations

import time
from typing import Callable, Iterable

DEFAULT_DEVICE_LABEL = "По умолчанию"


class DeviceWatcher:
    """Rate-limits audio-device polling and decides when to reopen a stream."""

    def __init__(
        self, interval: float = 4.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.interval = max(0.1, float(interval))
        self._clock = clock
        self._last = clock()

    def due(self) -> bool:
        """Return True at most once per interval seconds."""
        now = self._clock()
        if now - self._last >= self.interval:
            self._last = now
            return True
        return False

    @staticmethod
    def default_changed(
        follows_default: bool, opened_name: str, current_name: str | None
    ) -> bool:
        """Only a default-following stream reacts to default-device changes."""
        return bool(
            follows_default
            and current_name
            and current_name.casefold() != opened_name.casefold()
        )

    @staticmethod
    def selected_device_available(
        requested_device: str, available_names: Iterable[str] | None
    ) -> bool | None:
        """Return availability of a pinned device; None means query failed."""
        requested = (requested_device or "").strip()
        if not requested or requested == DEFAULT_DEVICE_LABEL:
            return True
        if available_names is None:
            return None
        requested_key = requested.casefold()
        return any(str(name).casefold() == requested_key for name in available_names)

    @classmethod
    def should_reopen(
        cls,
        *,
        opened_name: str,
        follows_default: bool,
        current_default_name: str | None,
        opened_selection: str,
        current_selection: str,
        requested_available_at_open: bool | None,
        requested_available_now: bool | None,
    ) -> bool:
        """Detect system-default, settings, and pinned-device availability changes."""
        if (opened_selection or "").casefold() != (current_selection or "").casefold():
            return True
        if cls.default_changed(follows_default, opened_name, current_default_name):
            return True
        if (
            requested_available_at_open is not None
            and requested_available_now is not None
            and requested_available_at_open != requested_available_now
        ):
            return True
        return False


class RetryBackoff:
    """Exponential retry delay with a hard cap and explicit success reset."""

    def __init__(
        self,
        initial_seconds: float = 1.0,
        maximum_seconds: float = 15.0,
        multiplier: float = 2.0,
    ) -> None:
        self.initial_seconds = max(0.1, float(initial_seconds))
        self.maximum_seconds = max(self.initial_seconds, float(maximum_seconds))
        self.multiplier = max(1.0, float(multiplier))
        self.failures = 0
        if self.multiplier == 1.0:
            self._maximum_step = 0
        else:
            import math
            self._maximum_step = max(
                0,
                math.ceil(
                    math.log(self.maximum_seconds / self.initial_seconds, self.multiplier)
                ),
            )

    def next_delay(self) -> float:
        step = min(self.failures, self._maximum_step)
        delay = min(
            self.maximum_seconds,
            self.initial_seconds * (self.multiplier ** step),
        )
        self.failures = min(self.failures + 1, self._maximum_step)
        return delay

    def reset(self) -> None:
        self.failures = 0
