"""Start at sign-in.

Windows: the per-user Run key. The installer writes the same value when its "Start
with Windows" box is ticked, so the installer and the Settings checkbox agree.
macOS: a LaunchAgent plist in ~/Library/LaunchAgents. The app turns it on at its
first launch (the Mac equivalent of the installer default), and Settings can turn
it off.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "MeetingRecorder"
MAC_LABEL = "com.meetingrecorder.app"
MAC_AGENT = Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"


def supported() -> bool:
    return sys.platform in ("win32", "darwin")


def label() -> str:
    return "Start when I log in" if sys.platform == "darwin" else "Start with Windows"


def arguments() -> list[str]:
    """What runs at sign-in: this app, opening hidden so it just watches for meetings."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--minimized"]
    exe = Path(sys.executable)
    # Source install on Windows: pythonw, so no console window flashes up at sign-in.
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if sys.platform == "win32" and pythonw.exists() else exe
    return [str(runner), "-m", "meeting_recorder", "--minimized"]


def command() -> str:
    """The Windows Run-key form of arguments()."""
    first, *rest = arguments()
    return " ".join([f'"{first}"', *rest])


def is_enabled() -> bool:
    if sys.platform == "darwin":
        return MAC_AGENT.exists()
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    if sys.platform == "darwin":
        _set_mac(enabled)
    elif sys.platform == "win32":
        _set_windows(enabled)


def _set_mac(enabled: bool) -> None:
    if not enabled:
        MAC_AGENT.unlink(missing_ok=True)
        return
    MAC_AGENT.parent.mkdir(parents=True, exist_ok=True)
    with open(MAC_AGENT, "wb") as f:
        plistlib.dump({
            "Label": MAC_LABEL,
            "ProgramArguments": arguments(),
            "RunAtLoad": True,
            "ProcessType": "Interactive",
        }, f)


def _set_windows(enabled: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
