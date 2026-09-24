"""User settings, stored as JSON next to the user's other app data."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path


def settings_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "MeetingRecorder"


SETTINGS_PATH = settings_dir() / "settings.json"


@dataclass
class Settings:
    # Where meeting folders go.
    output_dir: str = str(Path.home() / "Documents" / "Meetings")
    # How the transcript labels you and everyone else.
    your_name: str = "Me"
    others_label: str = "Others"
    # Audio devices, matched by (partial) name. None = Windows default device.
    mic_device: str | None = None
    speaker_device: str | None = None
    # Whisper records at 16 kHz internally, so recording at 16 kHz keeps files small.
    sample_rate: int = 16000
    # faster-whisper model: tiny, base, small, medium, large-v3, or distil-large-v3.
    whisper_model: str = "small"
    # "auto" tries the GPU first and falls back to CPU.
    whisper_device: str = "auto"
    # None = detect the language automatically. "en" skips detection.
    language: str | None = None
    # Watch for Zoom/Teams/Meet using the mic and offer to record.
    auto_detect: bool = True
    # Stop automatically once the meeting app releases the mic.
    auto_stop: bool = True
    auto_stop_grace_seconds: int = 20
    # Transcribe as soon as a recording stops.
    auto_process: bool = True

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> "Settings":
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path = SETTINGS_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
