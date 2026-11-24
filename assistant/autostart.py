"""Start-with-Windows via HKCU\\...\\Run (no admin rights needed)."""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("live_assistant.autostart")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "LocalAssistant"
LEGACY_VALUE_NAMES = ("LiveAssistant",)


def launch_command(
    frozen: bool | None = None,
    executable: str | None = None,
    script: str | None = None,
) -> str:
    """Command line to launch the app minimised to the tray."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = executable or sys.executable
    if frozen:
        return f'"{executable}" --tray'
    # Use pythonw so no console window flashes at logon.
    directory, name = os.path.split(executable)
    if name.lower() == "python.exe":
        candidate = os.path.join(directory, "pythonw.exe")
        if os.path.exists(candidate):
            executable = candidate
    script = script or os.path.abspath(sys.argv[0])
    return f'"{executable}" "{script}" --tray'


def _winreg(module=None):
    if module is not None:
        return module
    if sys.platform != "win32":
        return None
    import winreg
    return winreg


def is_enabled(winreg_module=None) -> bool:
    """Return whether LocalAssistant or a previous LiveAssistant entry exists."""
    reg = _winreg(winreg_module)
    if reg is None:
        return False
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as key:
            for name in (VALUE_NAME, *LEGACY_VALUE_NAMES):
                try:
                    reg.QueryValueEx(key, name)
                    return True
                except OSError:
                    continue
        return False
    except OSError:
        return False


def set_enabled(enabled: bool, winreg_module=None, command: str | None = None) -> bool:
    """Create/remove the Run entry; migrate the previous app name if enabled."""
    reg = _winreg(winreg_module)
    if reg is None:
        return False
    try:
        with reg.OpenKey(
            reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE
        ) as key:
            if enabled:
                reg.SetValueEx(
                    key, VALUE_NAME, 0, reg.REG_SZ, command or launch_command()
                )
            for name in LEGACY_VALUE_NAMES if enabled else (VALUE_NAME, *LEGACY_VALUE_NAMES):
                try:
                    reg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        logger.exception("Не удалось изменить автозапуск.")
        return False
