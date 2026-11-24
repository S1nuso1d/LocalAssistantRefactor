"""Device identity and selection helpers shared by audio capture sources.

SoundCard exposes backend-dependent IDs.  Prefer those stable IDs over display
names, while retaining a name-based compatibility path for existing settings.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Any

DEFAULT_DEVICE_LABEL = "По умолчанию"


def device_identity(device: Any) -> str:
    """Return a stable backend ID where available, otherwise a normalized name."""
    value = getattr(device, "id", None)
    if value is not None:
        text = str(value).strip()
        if text:
            return text
    return str(getattr(device, "name", "")).strip().casefold()


def device_name(device: Any) -> str:
    return str(getattr(device, "name", "Неизвестное устройство"))


def device_matches(device: Any, selected_id: str = "", selected_name: str = "") -> bool:
    wanted_id = str(selected_id or "").strip()
    wanted_name = str(selected_name or "").strip().casefold()
    actual_id = str(getattr(device, "id", "") or "").strip()
    actual_name = device_name(device).casefold()
    if wanted_id:
        # Once a backend ID has been saved, never silently bind to another
        # endpoint that happens to expose the same display name.
        return bool(actual_id and actual_id == wanted_id)
    return bool(wanted_name and actual_name == wanted_name)


@dataclass(frozen=True)
class DeviceSelection:
    device: Any
    follows_default: bool
    requested_available: bool
    requested_id: str
    requested_name: str


def resolve_device(
    devices: Iterable[Any],
    default_device: Any,
    selected_id: str = "",
    selected_name: str = DEFAULT_DEVICE_LABEL,
) -> DeviceSelection:
    """Resolve a saved selection or fall back to the current default device."""
    items = list(devices or [])
    requested_id = str(selected_id or "").strip()
    requested_name = str(selected_name or DEFAULT_DEVICE_LABEL).strip()
    explicit = bool(
        (requested_id and requested_id != DEFAULT_DEVICE_LABEL)
        or (requested_name and requested_name != DEFAULT_DEVICE_LABEL)
    )

    if explicit:
        selected = next(
            (item for item in items if device_matches(item, requested_id, requested_name)),
            None,
        )
        if selected is not None:
            return DeviceSelection(
                selected, False, True, requested_id, requested_name
            )
        if default_device is None:
            raise RuntimeError(
                f"Выбранное устройство «{requested_name or requested_id}» недоступно, "
                "а устройство по умолчанию не найдено."
            )
        return DeviceSelection(
            default_device, True, False, requested_id, requested_name
        )

    if default_device is None:
        raise RuntimeError("Windows не сообщает устройство по умолчанию.")
    return DeviceSelection(default_device, True, True, "", DEFAULT_DEVICE_LABEL)


def is_requested_device_available(
    devices: Iterable[Any], selected_id: str = "", selected_name: str = DEFAULT_DEVICE_LABEL
) -> bool:
    if not selected_id and (not selected_name or selected_name == DEFAULT_DEVICE_LABEL):
        return True
    return any(device_matches(item, selected_id, selected_name) for item in (devices or []))
