"""Stable, user-facing labels for audio endpoints."""
from __future__ import annotations

from collections import Counter
from typing import Iterable


def format_device_options(
    devices: Iterable[tuple[str, str]],
) -> list[tuple[str, str, str]]:
    """Return (display label, backend ID, original name) for a device list.

    Duplicate names are case-insensitive and get a short ID suffix. Full IDs
    remain in the combo-box tooltip; duplicate short suffixes fall back to the
    full ID, then an ordinal when the backend itself reports duplicate IDs.
    """
    normalized: list[tuple[str, str]] = []
    for raw_id, raw_name in devices:
        device_id = str(raw_id or "").strip()
        name = str(raw_name or "").strip()
        if name:
            normalized.append((device_id, name))

    name_counts = Counter(name.casefold() for _device_id, name in normalized)
    short_counts = Counter(
        (device_id[-8:].casefold() if device_id else "")
        for device_id, name in normalized
        if name_counts[name.casefold()] > 1 and device_id
    )
    full_counts = Counter(
        device_id.casefold()
        for device_id, name in normalized
        if name_counts[name.casefold()] > 1 and device_id
    )
    name_seen: Counter[str] = Counter()
    result: list[tuple[str, str, str]] = []

    for device_id, name in normalized:
        key = name.casefold()
        name_seen[key] += 1
        label = name
        if name_counts[key] > 1:
            short_id = device_id[-8:] if device_id else ""
            if not device_id:
                suffix = f"#{name_seen[key]}"
            elif short_counts[short_id.casefold()] == 1:
                suffix = f"…{short_id}"
            elif full_counts[device_id.casefold()] == 1:
                suffix = device_id
            else:
                suffix = f"{device_id} #{name_seen[key]}"
            label = f"{name} · {suffix}"
        result.append((label, device_id, name))
    return result
