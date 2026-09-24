"""On-disk layout of a meeting and the processing step that turns audio into transcripts.

Each meeting is one folder:

    2026-09-24_1430_Weekly sync/
        meta.json        title, start time, duration, status
        mic.wav          your microphone
        system.wav       everyone else (system audio)
        meeting.wav      both tracks mixed, for playback
        transcript.txt   plain text, easiest to paste into any AI tool
        transcript.md    formatted
        transcript.srt   subtitles, for use with meeting.wav in a media player
        transcript.json  segments with timestamps, for scripts
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import transcribe as tr
from .audio import mix_tracks
from .config import Settings

STATUS_RECORDING = "recording"
STATUS_RECORDED = "recorded"
STATUS_TRANSCRIBING = "transcribing"
STATUS_DONE = "done"
STATUS_ERROR = "error"


@dataclass
class Meeting:
    folder: Path
    title: str
    started_at: float
    duration: float = 0.0
    status: str = STATUS_RECORDING
    error: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def meta_path(self) -> Path:
        return self.folder / "meta.json"

    @property
    def transcript_txt(self) -> Path:
        return self.folder / "transcript.txt"

    @property
    def date_str(self) -> str:
        return datetime.fromtimestamp(self.started_at).strftime("%Y-%m-%d %H:%M")

    def save(self) -> None:
        data = asdict(self)
        data.pop("folder")
        self.meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, folder: Path) -> "Meeting":
        data = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        return cls(folder=folder, **data)


def safe_name(title: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip().rstrip(".")
    return cleaned[:60] or "Meeting"


def new_meeting(settings: Settings, title: str) -> Meeting:
    now = time.time()
    title = title.strip() or f"Meeting {datetime.fromtimestamp(now):%b %d %H:%M}"
    stamp = datetime.fromtimestamp(now).strftime("%Y-%m-%d_%H%M")
    base = Path(settings.output_dir) / f"{stamp}_{safe_name(title)}"
    folder, n = base, 2
    while folder.exists():
        folder = base.with_name(f"{base.name} ({n})")
        n += 1
    folder.mkdir(parents=True)
    meeting = Meeting(folder=folder, title=title, started_at=now)
    meeting.save()
    return meeting


def list_meetings(settings: Settings) -> list[Meeting]:
    root = Path(settings.output_dir)
    if not root.exists():
        return []
    out = []
    for d in root.iterdir():
        if (d / "meta.json").exists():
            try:
                out.append(Meeting.load(d))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
    return sorted(out, key=lambda m: m.started_at, reverse=True)


def process(meeting: Meeting, settings: Settings, model=None,
            progress: tr.ProgressFn | None = None,
            status: Callable[[str], None] | None = None) -> Meeting:
    """Transcribe a recorded meeting and write every export format. Safe to re-run."""
    meeting.status = STATUS_TRANSCRIBING
    meeting.error = None
    meeting.extra.pop("speaker_error", None)
    meeting.save()
    try:
        mic, system = meeting.folder / "mic.wav", meeting.folder / "system.wav"
        mixed = meeting.folder / "meeting.wav"
        if not mixed.exists():
            mix_tracks(mic, system, mixed)
        if model is None:
            model = tr.load_model(settings.whisper_model, settings.whisper_device)
        turns_fn = (lambda: _turns_or_none(meeting, system, None, status)) if settings.diarize else None
        segments = tr.transcribe_meeting(
            model, mic, system, settings.your_name, settings.others_label,
            settings.language, progress, turns_fn,
        )
        write_transcripts(meeting, segments)
        meeting.status = STATUS_DONE
    except Exception as exc:
        meeting.status = STATUS_ERROR
        meeting.error = f"{type(exc).__name__}: {exc}"
    meeting.save()
    return meeting


def _turns_or_none(meeting: Meeting, system: Path, num_speakers: int | None,
                   status: Callable[[str], None] | None):
    """Diarization is a bonus: if it fails (offline, say), keep "Others" and say why."""
    from . import diarize

    try:
        return diarize.diarize(system, num_speakers, status)
    except Exception as exc:
        meeting.extra["speaker_error"] = f"{type(exc).__name__}: {exc}"
        return None


# ---- speaker names ---------------------------------------------------------------

def load_transcript(meeting: Meeting) -> tuple[list[tr.Segment], dict[str, str]]:
    data = json.loads((meeting.folder / "transcript.json").read_text(encoding="utf-8"))
    segments = [tr.Segment.from_dict(d) for d in data["segments"]]
    names = data.get("speakers") or {s.speaker_id or s.speaker: s.speaker for s in segments}
    return segments, names


def speaker_names(segments: list[tr.Segment]) -> dict[str, str]:
    """Speaker id -> display name, in the order people first speak."""
    names: dict[str, str] = {}
    for seg in segments:
        names.setdefault(seg.speaker_id or seg.speaker, seg.speaker)
    return names


def rename_speaker(meeting: Meeting, speaker_id: str, name: str) -> None:
    """Rename everyone labeled speaker_id, in every transcript file."""
    segments, names = load_transcript(meeting)
    names[speaker_id] = name.strip() or names.get(speaker_id, speaker_id)
    tr.apply_names(segments, names)
    write_transcripts(meeting, segments, names)


def reassign_line(meeting: Meeting, index: int, name: str) -> None:
    """Give one line to a different person, existing or new, by display name."""
    segments, names = load_transcript(meeting)
    name = name.strip()
    if not name or not 0 <= index < len(segments):
        return
    existing = next((sid for sid, n in names.items() if n == name), None)
    if existing is None:
        n = 1
        while f"P{n}" in names:
            n += 1
        existing = f"P{n}"  # a person added by hand
        names[existing] = name
    segments[index].speaker_id = existing
    tr.apply_names(segments, names)
    write_transcripts(meeting, segments, names)


def regroup_speakers(meeting: Meeting, num_speakers: int | None,
                     status: Callable[[str], None] | None = None) -> None:
    """Split the call audio again with a given number of people (None = guess).

    Uses the saved word timings, so nothing is transcribed again. Names given to
    the other side are reset, since the groups change; your own name is kept.
    """
    segments, names = load_transcript(meeting)
    mine = [s for s in segments if s.speaker_id == tr.ME]
    theirs = [s for s in segments if s.speaker_id != tr.ME]
    turns = _turns_or_none(meeting, meeting.folder / "system.wav", num_speakers, status)
    if not turns:
        raise RuntimeError(meeting.extra.get("speaker_error") or "No speech found in the call audio")
    theirs = tr.assign_speakers(theirs, turns)
    merged = tr.merge_segments(mine + theirs)
    fresh = {tr.ME: names.get(tr.ME, "Me")}
    for seg in merged:
        fresh.setdefault(seg.speaker_id, tr.default_name(seg.speaker_id, fresh[tr.ME], "Others"))
    tr.apply_names(merged, fresh)
    write_transcripts(meeting, merged, fresh)
    meeting.save()


def write_transcripts(meeting: Meeting, segments: list[tr.Segment],
                      names: dict[str, str] | None = None) -> None:
    f = meeting.folder
    names = names or speaker_names(segments)
    (f / "transcript.txt").write_text(tr.to_text(segments), encoding="utf-8")
    (f / "transcript.md").write_text(
        tr.to_markdown(segments, meeting.title, meeting.date_str, meeting.duration), encoding="utf-8"
    )
    (f / "transcript.srt").write_text(tr.to_srt(segments), encoding="utf-8")
    (f / "transcript.json").write_text(
        json.dumps({"title": meeting.title, "started_at": meeting.started_at,
                    "speakers": names,
                    "segments": [s.to_dict() for s in segments]}, indent=2),
        encoding="utf-8",
    )
