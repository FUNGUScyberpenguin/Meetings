# PyInstaller build for MeetingRecorder.exe.
#   pyinstaller packaging/MeetingRecorder.spec --noconfirm
# Output: dist/MeetingRecorder/MeetingRecorder.exe plus its support files.
#
# One-folder mode on purpose: a one-file exe unpacks ~150 MB to a temp folder on every
# launch, starts slowly, and trips antivirus heuristics more often.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent

datas = [(str(root / "meeting_recorder" / "assets"), "meeting_recorder/assets")]
binaries = []
hiddenimports = []
# These packages load DLLs, ONNX models or C headers at runtime, which static
# analysis misses: ctranslate2 (Whisper engine), faster_whisper (VAD model),
# onnxruntime (runs the VAD), av (audio decoding), soundcard (WASAPI headers).
for pkg in ("ctranslate2", "faster_whisper", "onnxruntime", "av", "soundcard", "tokenizers"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(root / "packaging" / "launch.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["meeting_recorder.app", "meeting_recorder.selftest"],
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
    icon=str(root / "meeting_recorder" / "assets" / "icon.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MeetingRecorder", upx=False)
