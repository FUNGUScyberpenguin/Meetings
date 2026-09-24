"""Start-with-Windows, via the per-user Run key.

The installer writes the same value when its "Start with Windows" box is ticked, so
the Settings checkbox and the installer always agree on one source of truth.
"""

from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "MeetingRecorder"


def supported() -> bool:
    return sys.platform == "win32"


def command() -> str:
    """The command Windows runs at sign-in: this app, straight to the tray."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    # Source install: use pythonw so no console window flashes up at sign-in.
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else exe}" -m meeting_recorder --minimized'


def is_enabled() -> bool:
    if not supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    if not supported():
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
