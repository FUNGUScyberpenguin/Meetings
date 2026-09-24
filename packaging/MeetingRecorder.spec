# PyInstaller build for Meeting Recorder.
#   pyinstaller packaging/MeetingRecorder.spec --noconfirm
# Windows output: dist/MeetingRecorder/MeetingRecorder.exe plus its support files.
# macOS output:   dist/Meeting Recorder.app (build the Swift helper first, see
#                 scripts/build_macos.sh).
#
# One-folder mode on purpose: a one-file exe unpacks ~150 MB to a temp folder on every
# launch, starts slowly, and trips antivirus heuristics more often.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

sys.path.insert(0, str(Path(SPECPATH).parent))
root = Path(SPECPATH).parent
IS_MAC = sys.platform == "darwin"

datas = [(str(root / "meeting_recorder" / "assets"), "meeting_recorder/assets")]
binaries = []
hiddenimports = []
# These packages load DLLs, ONNX models or C headers at runtime, which static
# analysis misses: ctranslate2 (Whisper engine), faster_whisper (VAD model),
# onnxruntime (runs the VAD), av (audio decoding), soundcard (audio headers),
# sherpa_onnx (speaker separation), certifi (certificates for model downloads),
# pystray (picks its Windows backend by name at runtime; not used on macOS).
packages = ["ctranslate2", "faster_whisper", "onnxruntime", "av", "soundcard", "tokenizers",
            "sherpa_onnx", "certifi"]
if not IS_MAC:
    packages.append("pystray")
for pkg in packages:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

if IS_MAC:
    helper = root / "packaging" / "macos" / "build" / "mr-audio-helper"
    if not helper.exists():
        raise SystemExit(f"Build the Swift helper first: {helper} is missing (run scripts/build_macos.sh)")
    binaries.append((str(helper), "."))

a = Analysis(
    [str(root / "packaging" / "launch.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["meeting_recorder.app", "meeting_recorder.selftest", "meeting_recorder.tray"],
    excludes=["torch", "tensorflow", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MeetingRecorder",
    icon=str(root / "meeting_recorder" / "assets" / ("icon.png" if IS_MAC else "icon.ico")),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MeetingRecorder", upx=False)

if IS_MAC:
    import meeting_recorder

    app = BUNDLE(
        coll,
        name="Meeting Recorder.app",
        icon=str(root / "meeting_recorder" / "assets" / "icon.png"),
        bundle_identifier="com.meetingrecorder.app",
        version=meeting_recorder.__version__,
        info_plist={
            "CFBundleDisplayName": "Meeting Recorder",
            "CFBundleShortVersionString": meeting_recorder.__version__,
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
            # Shown in the macOS permission prompts.
            "NSMicrophoneUsageDescription":
                "Meeting Recorder records your microphone so your side of the meeting is in the transcript.",
            "NSAudioCaptureUsageDescription":
                "Meeting Recorder records the call audio so the other participants are in the transcript.",
        },
    )
