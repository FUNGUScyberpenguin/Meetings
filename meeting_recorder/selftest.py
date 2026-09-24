"""Checks that a packaged build has everything it needs.

    MeetingRecorder.exe --selftest REPORT.json [SPEECH.wav]

Writes a JSON report (a windowed exe has no console to print to) and exits non-zero
on failure. The build pipeline runs this against the finished exe, so a missing DLL
or data file fails the build instead of failing on a user's first meeting.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path


def run(report_path: Path, speech: Path | None = None) -> int:
    report: dict = {"ok": False}
    try:
        import av  # noqa: F401  (audio decoding used by faster-whisper)
        import ctranslate2
        import faster_whisper
        import onnxruntime  # noqa: F401  (voice activity detection)
        import soundfile

        report["versions"] = {
            "faster_whisper": faster_whisper.__version__,
            "ctranslate2": ctranslate2.__version__,
            "libsndfile": soundfile.__libsndfile_version__,
        }

        try:
            import soundcard  # noqa: F401
        except Exception:
            # Windows capture must work. Elsewhere soundcard needs PulseAudio, which a
            # build box may lack, so only note it.
            if sys.platform == "win32":
                raise
            report["soundcard_error"] = traceback.format_exc(limit=1)
        else:
            from .audio import list_devices

            try:
                report["devices"] = list_devices()
            except Exception as exc:  # build machines often have no audio hardware
                report["devices_error"] = f"{type(exc).__name__}: {exc}"

        if speech is not None:
            from .transcribe import load_model, transcribe_track

            model = load_model("tiny.en", "cpu")
            segments = transcribe_track(model, speech, "Test", "en")
            report["transcript"] = " ".join(s.text for s in segments)
            if not report["transcript"].strip():
                raise RuntimeError("transcription returned no text")
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


def main(args: list[str]) -> int:
    if not args:
        return 2
    return run(Path(args[0]), Path(args[1]) if len(args) > 1 else None)
