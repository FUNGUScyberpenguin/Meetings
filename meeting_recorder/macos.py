"""Talks to mr-audio-helper, the small Swift program that does the macOS-only work.

The helper handles ScreenCaptureKit (system audio), the Screen & System Audio
Recording permission, and asking CoreAudio which apps are using the microphone.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HELPER_NAME = "mr-audio-helper"
SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
PERMISSION_MESSAGE = (
    "Meeting Recorder needs Screen & System Audio Recording permission to hear the other "
    "people on the call. Turn it on in System Settings > Privacy & Security, then quit and "
    "reopen the app.")


def helper_path() -> Path:
    override = os.environ.get("MR_AUDIO_HELPER")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / HELPER_NAME
    # Running from source: scripts/build_macos.sh compiles it here.
    return Path(__file__).resolve().parent.parent / "packaging" / "macos" / "build" / HELPER_NAME


def _run(*args: str, timeout: float = 5) -> str:
    result = subprocess.run([str(helper_path()), *args], capture_output=True, text=True,
                            timeout=timeout, check=False)
    return result.stdout.strip()


def has_screen_permission() -> bool:
    try:
        return _run("permission") == "granted"
    except (OSError, subprocess.SubprocessError):
        return False


def request_screen_permission() -> bool:
    """Shows the macOS prompt the first time. Later calls just report the current state."""
    try:
        return _run("request-permission", timeout=120) == "granted"
    except (OSError, subprocess.SubprocessError):
        return False


def open_permission_settings() -> None:
    subprocess.run(["open", SETTINGS_URL], check=False)


def apps_using_mic() -> list[str]:
    try:
        return [line for line in _run("mic-users", timeout=3).splitlines() if line]
    except (OSError, subprocess.SubprocessError):
        return []


def start_capture(samplerate: int) -> subprocess.Popen:
    """Starts streaming system audio. Raises PermissionError or RuntimeError on failure."""
    path = helper_path()
    if not path.exists():
        raise RuntimeError(f"The macOS audio helper is missing: {path}")
    proc = subprocess.Popen([str(path), "capture", "--rate", str(samplerate)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stderr is not None
    first = proc.stderr.readline().decode("utf-8", "replace").strip()
    if first == "READY":
        return proc
    proc.wait(timeout=5)
    rest = proc.stderr.read().decode("utf-8", "replace").strip()
    detail = " ".join(x for x in (first, rest) if x)
    if proc.returncode == 3 or first.startswith("PERMISSION_DENIED"):
        raise PermissionError(PERMISSION_MESSAGE)
    raise RuntimeError(f"System audio capture failed to start: {detail or proc.returncode}")
