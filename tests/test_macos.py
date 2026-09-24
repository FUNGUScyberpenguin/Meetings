"""The macOS plumbing, tested against a stand-in for the Swift helper.

The fake speaks the same protocol as packaging/macos/AudioHelper.swift: READY on
stderr, raw float32 samples on stdout, stop when stdin closes, exit 3 when the
Screen Recording permission is missing.
"""

import plistlib
import stat
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from meeting_recorder import audio, detect, macos, startup

# The fake helper is a #! script, which Windows can't execute directly.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell script")

FAKE_HELPER = """#!{python}
import os, sys, threading, time
import numpy as np
cmd = sys.argv[1]
mode = os.environ.get("FAKE_MODE", "ok")
if cmd == "version": print("1")
elif cmd == "permission": print("denied" if mode == "denied" else "granted")
elif cmd == "request-permission": print("denied" if mode == "denied" else "granted")
elif cmd == "mic-users": print("com.meetingrecorder.app"); print("us.zoom.xos")
elif cmd == "capture":
    if mode == "denied":
        sys.stderr.write("PERMISSION_DENIED user declined\\n"); sys.exit(3)
    rate = int(sys.argv[sys.argv.index("--rate") + 1])
    sys.stderr.write("READY\\n"); sys.stderr.flush()
    done = threading.Event()
    threading.Thread(target=lambda: (sys.stdin.read(), done.set()), daemon=True).start()
    chunk = np.full(rate // 20, 0.25, dtype="<f4").tobytes()  # 50 ms of a constant tone
    while not done.is_set():
        try:
            sys.stdout.buffer.write(chunk); sys.stdout.buffer.flush()
        except BrokenPipeError:
            break
        time.sleep(0.05)
"""


@pytest.fixture
def fake_helper(tmp_path, monkeypatch):
    path = tmp_path / "mr-audio-helper"
    path.write_text(FAKE_HELPER.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("MR_AUDIO_HELPER", str(path))
    return path


def test_helper_queries(fake_helper):
    assert macos.helper_path() == fake_helper
    assert macos.has_screen_permission()
    assert macos.apps_using_mic() == ["com.meetingrecorder.app", "us.zoom.xos"]
    # Our own bundle ID is ignored; Zoom is found.
    assert detect.match_meeting_app(macos.apps_using_mic()) == "Zoom"


def test_permission_denied_is_a_clear_error(fake_helper, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "denied")
    assert not macos.has_screen_permission()
    with pytest.raises(PermissionError, match="Screen & System Audio Recording"):
        macos.start_capture(16000)


def test_missing_helper_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("MR_AUDIO_HELPER", str(tmp_path / "nope"))
    assert not macos.has_screen_permission()
    assert macos.apps_using_mic() == []
    with pytest.raises(RuntimeError, match="missing"):
        macos.start_capture(16000)


def test_system_audio_track_records_and_stops(fake_helper, tmp_path):
    source = audio.MacSystemAudio()
    stop = threading.Event()
    out = tmp_path / "system.wav"
    track = audio._TrackThread(source, out, 16000, time.monotonic(), stop)
    track.start()
    time.sleep(1.5)
    stop.set()
    source.close()  # what Recorder.stop() does; unblocks a read waiting on audio
    track.join(timeout=5)

    assert not track.is_alive()
    assert track.error is None
    data, sr = sf.read(out)
    assert sr == 16000
    assert 1.0 * sr < len(data) < 2.5 * sr
    # The track starts with silence while the helper launches (padding that keeps it
    # aligned with the mic), then carries the helper's constant 0.25 tone.
    audio_part = data[np.abs(data) > 1e-4]
    assert len(audio_part) > 0.2 * sr
    assert np.allclose(audio_part, 0.25, atol=0.01)
    assert track.level > 0.2


def test_bundle_ids_match_meeting_apps():
    assert detect.match_meeting_app(["com.microsoft.teams2"]) == "Microsoft Teams"
    assert detect.match_meeting_app(["com.google.Chrome.helper"]) == "Chrome (browser meeting)"
    assert detect.match_meeting_app(["com.apple.Music"]) is None


def test_mac_login_item(tmp_path, monkeypatch):
    agent = tmp_path / "LaunchAgents" / "com.meetingrecorder.app.plist"
    monkeypatch.setattr(startup, "MAC_AGENT", agent)
    monkeypatch.setattr(startup.sys, "platform", "darwin")
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(startup.sys, "executable", "/Applications/Meeting Recorder.app/Contents/MacOS/MeetingRecorder")

    assert startup.label() == "Start when I log in"
    assert not startup.is_enabled()
    startup.set_enabled(True)
    plist = plistlib.loads(agent.read_bytes())
    assert plist["ProgramArguments"] == [
        "/Applications/Meeting Recorder.app/Contents/MacOS/MeetingRecorder", "--minimized"]
    assert plist["RunAtLoad"] is True
    assert startup.is_enabled()
    startup.set_enabled(False)
    assert not agent.exists()
