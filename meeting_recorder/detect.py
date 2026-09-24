"""Detect when a meeting app starts using the microphone (Windows only).

Windows logs microphone use per app under
HKCU\\...\\CapabilityAccessManager\\ConsentStore\\microphone. An app is using the mic
right now when its LastUsedTimeStart is set and LastUsedTimeStop is 0. That is the
same data behind the mic icon in the taskbar, and it needs no admin rights.
"""

from __future__ import annotations

import sys

MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"

# Substring (lowercase) of the app path or package name -> display name.
# Browsers are included because Google Meet and the web versions of Zoom/Teams run there.
MEETING_APPS = {
    "zoom": "Zoom",
    "msteams": "Microsoft Teams",
    "teams.exe": "Microsoft Teams",
    "webex": "Webex",
    "ciscocollab": "Webex",
    "slack": "Slack",
    "discord": "Discord",
    "skype": "Skype",
    "gotomeeting": "GoTo Meeting",
    "g2mcomm": "GoTo Meeting",
    "ringcentral": "RingCentral",
    "bluejeans": "BlueJeans",
    "chrome.exe": "Chrome (browser meeting)",
    "msedge.exe": "Edge (browser meeting)",
    "firefox.exe": "Firefox (browser meeting)",
    "brave.exe": "Brave (browser meeting)",
    "opera.exe": "Opera (browser meeting)",
    "arc.exe": "Arc (browser meeting)",
}


def match_meeting_app(apps_in_use: list[str]) -> str | None:
    """Return a display name for the first meeting app in the list, ignoring this program."""
    for app in apps_in_use:
        low = app.lower()
        if "python" in low:  # our own recorder holds the mic while recording
            continue
        for needle, name in MEETING_APPS.items():
            if needle in low:
                return name
    return None


def apps_using_mic() -> list[str]:
    if sys.platform != "win32":
        return []
    import winreg

    found: list[str] = []

    def scan(path: str) -> None:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        with key:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                if name == "NonPackaged":
                    scan(f"{path}\\{name}")
                    continue
                try:
                    with winreg.OpenKey(key, name) as sub:
                        start, _ = winreg.QueryValueEx(sub, "LastUsedTimeStart")
                        stop, _ = winreg.QueryValueEx(sub, "LastUsedTimeStop")
                except OSError:
                    continue
                if start and stop == 0:
                    found.append(name.replace("#", "\\"))

    scan(MIC_KEY)
    return found


def active_meeting_app() -> str | None:
    return match_meeting_app(apps_using_mic())
