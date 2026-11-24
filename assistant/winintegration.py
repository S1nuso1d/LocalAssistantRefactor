"""Windows window-affinity helpers (hide a window from screen capture).

SetWindowDisplayAffinity protects the application's own top-level windows
from a supported set of Windows capture APIs. It is a privacy aid, not DRM:
it cannot prevent cameras, hardware capture, or every third-party capture path.

The module is pure ctypes with an injectable user32 object for unit tests.
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

logger = logging.getLogger("live_assistant.win")

WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011

HIDDEN = "hidden"
BLACK = "black"
VISIBLE = "visible"
UNSUPPORTED = "unsupported"
FAILED = "failed"


def _default_user32(platform: str):
    if platform != "win32":
        return None

    # use_last_error=True is required for ctypes.get_last_error() to return
    # the error raised by the most recent Win32 call made through this DLL.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.GetWindowDisplayAffinity.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
    return user32


def _reset_last_error() -> None:
    setter = getattr(ctypes, "set_last_error", None)
    if setter is not None:
        setter(0)


def _last_error_code() -> int:
    getter = getattr(ctypes, "get_last_error", None)
    if getter is None:
        return 0
    try:
        return int(getter())
    except Exception:
        return 0


def format_win32_error(code: int) -> str:
    """Format a captured Win32 error code without relying on Windows-only APIs."""
    code = int(code or 0)
    if code <= 0:
        return "Windows не предоставила код ошибки"
    formatter = getattr(ctypes, "FormatError", None)
    if formatter is not None:
        try:
            message = str(formatter(code)).strip()
            if message:
                return f"WinError {code}: {message}"
        except Exception:
            pass
    return f"WinError {code}"


def _set_affinity(api, hwnd: int, affinity: int) -> tuple[bool, int]:
    _reset_last_error()
    succeeded = bool(api.SetWindowDisplayAffinity(hwnd, affinity))
    return succeeded, _last_error_code()


def _get_affinity(api, hwnd: int):
    value = wintypes.DWORD(0)
    _reset_last_error()
    succeeded = bool(api.GetWindowDisplayAffinity(hwnd, ctypes.byref(value)))
    return succeeded, int(value.value), _last_error_code()


def set_capture_hidden(
    hwnd: int, hidden: bool, user32=None, platform: str = sys.platform
) -> str:
    """Apply capture affinity to a top-level HWND and return the resulting state.

    WDA_EXCLUDEFROMCAPTURE is attempted first. WDA_MONITOR is retained as a
    compatibility fallback; its captured representation is a black rectangle.
    Failed calls include the HWND and Win32 error code in the application log.
    """
    if platform != "win32":
        return UNSUPPORTED

    api = user32 or _default_user32(platform)
    try:
        hwnd = int(hwnd)
        if not hidden:
            succeeded, error_code = _set_affinity(api, hwnd, WDA_NONE)
            if succeeded:
                return VISIBLE
            logger.warning(
                "Не удалось снять защиту захвата для hwnd=0x%X: %s",
                hwnd, format_win32_error(error_code),
            )
            return FAILED

        modern_ok, modern_error = _set_affinity(
            api, hwnd, WDA_EXCLUDEFROMCAPTURE
        )
        if modern_ok:
            return HIDDEN

        fallback_ok, fallback_error = _set_affinity(api, hwnd, WDA_MONITOR)
        if fallback_ok:
            logger.warning(
                "WDA_EXCLUDEFROMCAPTURE отклонён для hwnd=0x%X (%s); "
                "включён резервный режим WDA_MONITOR (чёрный прямоугольник).",
                hwnd, format_win32_error(modern_error),
            )
            return BLACK

        logger.warning(
            "Windows отклонила защиту захвата для hwnd=0x%X. "
            "WDA_EXCLUDEFROMCAPTURE: %s; WDA_MONITOR: %s",
            hwnd, format_win32_error(modern_error),
            format_win32_error(fallback_error),
        )
        return FAILED
    except Exception:
        logger.exception(
            "Ошибка SetWindowDisplayAffinity для hwnd=0x%X.",
            int(hwnd) if isinstance(hwnd, int) else 0,
        )
        return FAILED


def get_capture_state(
    hwnd: int, user32=None, platform: str = sys.platform
) -> str:
    """Read back the affinity currently in force for hwnd."""
    if platform != "win32":
        return UNSUPPORTED

    api = user32 or _default_user32(platform)
    try:
        hwnd = int(hwnd)
        succeeded, value, error_code = _get_affinity(api, hwnd)
        if not succeeded:
            logger.warning(
                "Не удалось прочитать защиту захвата для hwnd=0x%X: %s",
                hwnd, format_win32_error(error_code),
            )
            return FAILED
        if value == WDA_EXCLUDEFROMCAPTURE:
            return HIDDEN
        if value == WDA_MONITOR:
            return BLACK
        if value == WDA_NONE:
            return VISIBLE
        logger.warning(
            "Неизвестное значение display affinity для hwnd=0x%X: 0x%X",
            hwnd, value,
        )
        return FAILED
    except Exception:
        logger.exception(
            "Ошибка GetWindowDisplayAffinity для hwnd=0x%X.",
            int(hwnd) if isinstance(hwnd, int) else 0,
        )
        return FAILED


class AffinityCache:
    """Remembers which window handles were already protected.

    Windows recycles HWND values after a window is destroyed, so a cached
    "protected" verdict is only valid for the *same* native window. Callers
    must ``forget`` a handle whenever Qt reports that a widget received a
    (new) native handle; otherwise a fresh tooltip or menu that happens to get
    a recycled HWND would silently skip protection and be visible in a capture.
    """

    def __init__(self, limit: int = 512) -> None:
        self._limit = max(1, int(limit))
        self._states: dict[int, str] = {}
        self._touched: set[int] = set()
        # Only handles owned by live Qt widgets are retained here.
        # CaptureGuard removes them when QObject.destroyed is emitted.
        self._owners: dict[int, int] = {}

    def state(self, hwnd: int) -> str | None:
        return self._states.get(hwnd)

    def touched(self, hwnd: int) -> bool:
        """True if protection was ever applied to this handle (so a later
        "off" must actively reset it)."""
        return hwnd in self._touched

    def record(
        self, hwnd: int, state: str, *, hidden: bool, owner: int | None = None
    ) -> None:
        if len(self._states) >= self._limit and hwnd not in self._states:
            # Bound state snapshots, but never discard handles that still
            # need their capture affinity reset when protection is disabled.
            self._states.clear()

        self._states[hwnd] = state

        if hidden:
            self._touched.add(hwnd)
            if owner is None:
                self._owners.pop(hwnd, None)
            else:
                self._owners[hwnd] = owner
        elif state == VISIBLE:
            # Remove a handle only after Windows confirms that protection
            # has been successfully disabled. Failed resets remain retryable.
            self._touched.discard(hwnd)
            self._owners.pop(hwnd, None)

    def owns(self, owner: int) -> bool:
        """Whether this widget owns a handle that still needs a reset."""
        return owner in self._owners.values()

    def forget_owner(self, owner: int) -> None:
        """Forget every protected HWND belonging to a destroyed/recreated widget."""
        handles = [
            hwnd for hwnd, tracked_owner in self._owners.items()
            if tracked_owner == owner
        ]
        for hwnd in handles:
            self.forget(hwnd)

    def forget(self, hwnd: int) -> None:
        self._states.pop(hwnd, None)
        self._touched.discard(hwnd)
        self._owners.pop(hwnd, None)


def apply_protection(
    cache: AffinityCache,
    hwnd: int,
    hidden: bool,
    *,
    force: bool = False,
    owner: int | None = None,
    apply=None,
) -> tuple[str | None, str | None]:
    """Decide whether to call Win32 for *hwnd*; return (new_state, previous).

    ``new_state`` is None when nothing had to be done. ``force`` must be True
    when the window just received a native handle (see AffinityCache).
    """
    apply = apply or set_capture_hidden
    if force:
        cache.forget(hwnd)
    if not hidden and not cache.touched(hwnd):
        return None, None  # never protected, nothing to undo
    previous = cache.state(hwnd)
    if hidden and previous in (HIDDEN, BLACK):
        return previous, previous  # already protected: skip repeated calls
    state = apply(hwnd, hidden)
    cache.record(hwnd, state, hidden=hidden, owner=owner)
    return state, previous


STATE_TEXT = {
    HIDDEN: "Активно: окно исключено из поддерживаемых способов захвата Windows",
    BLACK: "Частично: в записи окно отображается чёрным прямоугольником",
    VISIBLE: "Выключено: окно видно при захвате экрана",
    UNSUPPORTED: "Недоступно: функция работает только в Windows",
    FAILED: "Windows не применила защиту; подробности и код ошибки записаны в журнал",
}
