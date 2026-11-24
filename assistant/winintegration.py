"""Windows window-affinity helpers (hide a window from screen capture).

SetWindowDisplayAffinity is the same API used by password managers and
presentation tools. It excludes a window from software capture (OBS, Zoom,
Teams, Discord, the Windows Snipping Tool, DXGI/BitBlt screenshots). It does
NOT affect a camera pointed at the monitor or a hardware HDMI capture card.

The module is pure ctypes with an injectable ``user32`` so it is unit-tested
on any OS.
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

logger = logging.getLogger("live_assistant.win")

WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001  # shown as a black rectangle in captures
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # invisible in captures (Windows 10 2004+)

# Result states
HIDDEN = "hidden"            # excluded from capture (window simply absent)
BLACK = "black"              # legacy mode: captured as a black rectangle
VISIBLE = "visible"          # protection is off
UNSUPPORTED = "unsupported"  # not Windows
FAILED = "failed"            # the API refused


def _default_user32(platform: str):
    if platform != "win32":
        return None
    user32 = ctypes.windll.user32
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.GetWindowDisplayAffinity.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
    return user32


def set_capture_hidden(
    hwnd: int, hidden: bool, user32=None, platform: str = sys.platform
) -> str:
    """Hide/show *hwnd* in screen captures and return the resulting state.

    Tries the modern invisible mode first and falls back to the legacy
    black-rectangle mode on Windows older than 10 version 2004.
    """
    if platform != "win32":
        return UNSUPPORTED
    api = user32 or _default_user32(platform)
    try:
        if not hidden:
            return VISIBLE if api.SetWindowDisplayAffinity(hwnd, WDA_NONE) else FAILED
        if api.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
            return HIDDEN
        if api.SetWindowDisplayAffinity(hwnd, WDA_MONITOR):
            return BLACK
        return FAILED
    except Exception:
        logger.exception("SetWindowDisplayAffinity завершилась ошибкой.")
        return FAILED


def get_capture_state(
    hwnd: int, user32=None, platform: str = sys.platform
) -> str:
    """Read back the affinity actually in force for *hwnd*."""
    if platform != "win32":
        return UNSUPPORTED
    api = user32 or _default_user32(platform)
    try:
        value = wintypes.DWORD(0)
        if not api.GetWindowDisplayAffinity(hwnd, ctypes.byref(value)):
            return FAILED
        if value.value == WDA_EXCLUDEFROMCAPTURE:
            return HIDDEN
        if value.value == WDA_MONITOR:
            return BLACK
        return VISIBLE
    except Exception:
        logger.exception("GetWindowDisplayAffinity завершилась ошибкой.")
        return FAILED


STATE_TEXT = {
    HIDDEN: "Активно: окно не попадает в запись и демонстрацию экрана",
    BLACK: "Частично: на старой Windows окно видно в записи как чёрный прямоугольник",
    VISIBLE: "Выключено: окно видно в записи экрана",
    UNSUPPORTED: "Недоступно: функция работает только в Windows",
    FAILED: "Не удалось включить защиту (Windows отклонила запрос)",
}
