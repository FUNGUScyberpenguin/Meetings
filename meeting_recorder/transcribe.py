"""Local speech-to-text with faster-whisper, plus merging the two tracks into one transcript."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

import soundfile as sf

ProgressFn = Callable[[float, str], None]


@dataclass
class Segment:
    start: float
    end: float
    speaker: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def load_model(model_name: str, device: str = "auto"):
    from faster_whisper import WhisperModel

    if device in ("auto", "cuda"):
        try:
            return WhisperModel(model_name, device="cuda", compute_type="float16")
        except Exception:
            if device == "cuda":
                raise
    # A GPU without the CUDA libraries installed lands here too.
    return WhisperModel(model_name, device="cpu", compute_type="int8")


def _duration(path: Path) -> float:
    try:
        info = sf.info(str(path))
        return info.frames / info.samplerate
    except Exception:
        return 0.0


def transcribe_track(model, path: Path, speaker: str, language: str | None,
                     progress: ProgressFn | None = None) -> list[Segment]:
    if not path.exists() or _duration(path) < 0.5:
        return []
    segments, info = model.transcribe(
        str(path),
        language=language,
        # VAD skips long silences, which is most of the mic track in a typical call,
        # and stops Whisper from hallucinating text into silence.
        vad_filter=True,
        beam_size=5,
        condition_on_previous_text=False,
    )
    out: list[Segment] = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            out.append(Segment(seg.start, seg.end, speaker, text))
        if progress and info.duration:
            progress(min(seg.end / info.duration, 1.0), speaker)
    if progress:
        progress(1.0, speaker)
    return out


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def drop_echo(mine: list[Segment], theirs: list[Segment],
              threshold: float = 0.6, slack: float = 1.5) -> list[Segment]:
    """Remove mic segments that are just the other side's audio leaking from speakers.

    Without headphones the mic picks up the call audio, so the same sentence shows
    up on both tracks. Keep it only on the system track.
    """
    kept = []
    for m in mine:
        echoed = any(
            t.start - slack <= m.end and m.start <= t.end + slack and _similar(m.text, t.text) >= threshold
            for t in theirs
        )
        if not echoed:
            kept.append(m)
    return kept


def merge_segments(segments: list[Segment], max_gap: float = 1.5) -> list[Segment]:
    """Sort by time and join consecutive lines from the same speaker into one turn."""
    merged: list[Segment] = []
    for seg in sorted(segments, key=lambda s: s.start):
        last = merged[-1] if merged else None
        if last and last.speaker == seg.speaker and seg.start - last.end <= max_gap:
            last.end = max(last.end, seg.end)
            last.text = f"{last.text} {seg.text}"
        else:
            merged.append(Segment(seg.start, seg.end, seg.speaker, seg.text))
    return merged


def transcribe_meeting(model, mic: Path, system: Path, your_name: str, others_label: str,
                       language: str | None, progress: ProgressFn | None = None) -> list[Segment]:
    def half(offset: float) -> ProgressFn | None:
        if progress is None:
            return None
        return lambda frac, who: progress(offset + frac / 2, who)

    mine = transcribe_track(model, mic, your_name, language, half(0.0))
    theirs = transcribe_track(model, system, others_label, language, half(0.5))
    return merge_segments(drop_echo(mine, theirs) + theirs)


# ---- export formats -------------------------------------------------------------

def fmt_clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fmt_srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


def to_text(segments: list[Segment]) -> str:
    return "\n\n".join(f"[{fmt_clock(s.start)}] {s.speaker}: {s.text}" for s in segments) + "\n"


def to_markdown(segments: list[Segment], title: str, date_str: str, duration: float) -> str:
    header = f"# {title}\n\n{date_str} · {fmt_clock(duration)}\n\n"
    body = "\n\n".join(f"**{s.speaker}** `{fmt_clock(s.start)}`  \n{s.text}" for s in segments)
    return header + body + "\n"


def to_srt(segments: list[Segment]) -> str:
    blocks = [
        f"{i}\n{fmt_srt_time(s.start)} --> {fmt_srt_time(s.end)}\n{s.speaker}: {s.text}\n"
        for i, s in enumerate(segments, 1)
    ]
    return "\n".join(blocks)
