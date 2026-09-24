"""Local speech-to-text with faster-whisper, plus merging the two tracks into one transcript."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import soundfile as sf

if TYPE_CHECKING:
    from .diarize import Turn

ProgressFn = Callable[[float, str], None]
# Speaker ids: "me" (mic), "others" (call audio, not split), "S1", "S2"... (split by voice)
ME, OTHERS = "me", "others"


@dataclass
class Segment:
    start: float
    end: float
    speaker: str        # display name
    text: str
    speaker_id: str = ""
    # (start, end, word) for call-audio lines, kept so speakers can be re-grouped
    # later without transcribing again.
    words: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        return cls(d["start"], d["end"], d.get("speaker", ""), d["text"],
                   d.get("speaker_id", ""), [tuple(w) for w in d.get("words", [])])


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
                     progress: ProgressFn | None = None, speaker_id: str = "",
                     words: bool = False) -> list[Segment]:
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
        word_timestamps=words,
    )
    out: list[Segment] = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            ws = [(w.start, w.end, w.word) for w in (getattr(seg, "words", None) or [])] if words else []
            out.append(Segment(seg.start, seg.end, speaker, text, speaker_id, ws))
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
        same = (last is not None and (last.speaker_id or last.speaker) == (seg.speaker_id or seg.speaker))
        if same and seg.start - last.end <= max_gap:
            last.end = max(last.end, seg.end)
            last.text = f"{last.text} {seg.text}"
            last.words = last.words + list(seg.words)
        else:
            merged.append(Segment(seg.start, seg.end, seg.speaker, seg.text, seg.speaker_id, list(seg.words)))
    return merged


def default_name(speaker_id: str, your_name: str, others_label: str) -> str:
    if speaker_id == ME:
        return your_name
    if speaker_id.startswith("S") and speaker_id[1:].isdigit():
        return f"Speaker {speaker_id[1:]}"
    return others_label


def _speaker_at(turns: list["Turn"], t: float, previous: int | None) -> int | None:
    best, best_gap = previous, 1.0  # a word within 1 s of a turn belongs to it
    for turn in turns:
        if turn.start <= t <= turn.end:
            return turn.speaker
        gap = min(abs(t - turn.start), abs(t - turn.end))
        if gap < best_gap:
            best, best_gap = turn.speaker, gap
    return best


def assign_speakers(segments: list[Segment], turns: list["Turn"]) -> list[Segment]:
    """Split call-audio lines by who spoke each word, using the diarization turns."""
    if not turns:
        return segments
    out: list[Segment] = []
    for seg in segments:
        words = seg.words or [(seg.start, seg.end, seg.text)]
        previous: int | None = None
        run: list = []
        run_speaker: int | None = None
        for w in words:
            spk = _speaker_at(turns, (w[0] + w[1]) / 2, previous)
            if run and spk != run_speaker:
                out.append(_from_words(run, run_speaker))
                run = []
            run.append(w)
            run_speaker = previous = spk
        if run:
            out.append(_from_words(run, run_speaker))
    return out


def _from_words(words: list, speaker: int | None) -> Segment:
    sid = f"S{speaker + 1}" if speaker is not None else OTHERS
    text = "".join(w[2] for w in words).strip()
    return Segment(words[0][0], words[-1][1], "", text, sid, list(words))


def apply_names(segments: list[Segment], names: dict[str, str]) -> None:
    for seg in segments:
        seg.speaker = names.get(seg.speaker_id, seg.speaker)


def transcribe_meeting(model, mic: Path, system: Path, your_name: str, others_label: str,
                       language: str | None, progress: ProgressFn | None = None,
                       turns_fn: Callable[[], list["Turn"] | None] | None = None) -> list[Segment]:
    """Transcribes both tracks. turns_fn, if given, returns diarization turns for the
    call audio (or None if unavailable) and is used to split "Others" by voice."""
    def half(offset: float) -> ProgressFn | None:
        if progress is None:
            return None
        return lambda frac, who: progress(offset + frac / 2, who)

    mine = transcribe_track(model, mic, your_name, language, half(0.0), speaker_id=ME)
    theirs = transcribe_track(model, system, others_label, language, half(0.5),
                              speaker_id=OTHERS, words=turns_fn is not None)
    mine = drop_echo(mine, theirs)
    turns = turns_fn() if turns_fn and theirs else None
    if turns:
        theirs = assign_speakers(theirs, turns)
    merged = merge_segments(mine + theirs)
    apply_names(merged, {s.speaker_id: default_name(s.speaker_id, your_name, others_label)
                         for s in merged})
    return merged


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
