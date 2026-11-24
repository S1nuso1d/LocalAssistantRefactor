"""Single source of truth for the application version."""
from __future__ import annotations

import sys
from pathlib import Path


UNKNOWN_VERSION = "unknown"


def read_app_version(root: str | Path | None = None) -> str:
    """Read VERSION beside the source tree or the frozen executable.

    PyInstaller's onedir build places VERSION next to LocalAssistant.exe;
    source runs read it from the repository root.
    """
    if root is not None:
        base = Path(root)
    elif getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parents[1]

    try:
        value = (base / "VERSION").read_text(encoding="utf-8-sig").strip()
    except OSError:
        return UNKNOWN_VERSION
    return value or UNKNOWN_VERSION


APP_VERSION = read_app_version()
