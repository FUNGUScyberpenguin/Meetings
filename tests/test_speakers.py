"""Splitting the other side of the call by voice, and naming people."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from meeting_recorder import diarize, meetings
from meeting_recorder import transcribe as tr
from meeting_recorder.config import Settings
from meeting_recorder.diarize import Turn


def words(*items):
    return [(s, e, w) for s, e, w in items]


def test_renumber_orders_speakers_by_first_appearance():
    turns = diarize.renumber([Turn(5, 6, 3), Turn(0, 1, 7), Turn(2, 3, 3)])
    assert [t.speaker for t in turns] == [0, 1, 1]


def test_assign_splits_a_line_where_the_voice_changes():
    seg = tr.Segment(0, 6, "Others", "yes agreed okay so the budget", tr.OTHERS,
                     words((0.0, 0.5, " yes"), (0.5, 1.0, " agreed"),
                           (3.0, 3.4, " okay"), (3.4, 3.8, " so"), (4.0, 4.5, " the"), (4.5, 5.0, " budget")))
    turns = [Turn(0.0, 1.2, 0), Turn(2.9, 5.2, 1)]
    out = tr.assign_speakers([seg], turns)
    assert [(s.speaker_id, s.text) for s in out] == [("S1", "yes agreed"), ("S2", "okay so the budget")]
    assert out[1].start == 3.0 and out[1].end == 5.0


def test_words_in_gaps_stay_with_the_nearest_or_previous_speaker():
    seg = tr.Segment(0, 10, "Others", "", tr.OTHERS,
                     words((0.0, 0.5, " one"), (1.4, 1.6, " two"), (9.0, 9.5, " three")))
    out = tr.assign_speakers([seg], [Turn(0.0, 1.0, 0), Turn(5.0, 6.0, 1)])
    # "two" is 0.5 s after speaker 1's turn; "three" is 3 s from anything, so it
    # stays with the previous speaker rather than jumping to a new one.
    assert [(s.speaker_id, s.text) for s in out] == [("S1", "one two three")]


class WordModel:
    """Fake Whisper: canned segments per file, with word timings for the call audio."""

    def __init__(self, by_file):
        self.by_file = by_file

    def transcribe(self, path, word_timestamps=False, **_):
        segs = []
        for s, e, text in self.by_file[Path(path).name]:
            ws = text.split()
            step = (e - s) / len(ws)
            w = [SimpleNamespace(start=s + i * step, end=s + (i + 1) * step, word=" " + x)
                 for i, x in enumerate(ws)] if word_timestamps else None
            segs.append(SimpleNamespace(start=s, end=e, text=" " + text, words=w))
        return iter(segs), SimpleNamespace(duration=30.0)


def recorded_meeting(tmp_path, monkeypatch, turns):
    settings = Settings(output_dir=str(tmp_path), your_name="Josh")
    m = meetings.new_meeting(settings, "Planning")
    for name in ("mic.wav", "system.wav"):
        sf.write(m.folder / name, np.zeros(16000 * 20, dtype="float32"), 16000)
    monkeypatch.setattr(diarize, "diarize", lambda path, n=None, status=None: turns(n))
    model = WordModel({
        "mic.wav": [(0.0, 2.0, "Morning all, let's start")],
        "system.wav": [(3.0, 6.0, "The numbers are in"), (7.0, 9.0, "Great, what about hiring"),
                       (10.0, 12.0, "Hiring is on track")],
    })
    return settings, meetings.process(m, settings, model)


def three_people(n):
    if n == 1:
        return [Turn(2.5, 12.5, 0)]
    return [Turn(2.5, 6.5, 0), Turn(6.8, 9.5, 1), Turn(9.8, 12.5, 0)]


def test_process_names_each_voice(tmp_path, monkeypatch):
    _settings, m = recorded_meeting(tmp_path, monkeypatch, three_people)
    assert m.status == meetings.STATUS_DONE, m.error
    txt = (m.folder / "transcript.txt").read_text()
    assert "Josh: Morning all" in txt
    assert "Speaker 1: The numbers are in" in txt
    assert "Speaker 2: Great, what about hiring" in txt
    assert "Speaker 1: Hiring is on track" in txt
    data = json.loads((m.folder / "transcript.json").read_text())
    assert data["speakers"] == {"me": "Josh", "S1": "Speaker 1", "S2": "Speaker 2"}


def test_rename_updates_every_file(tmp_path, monkeypatch):
    _settings, m = recorded_meeting(tmp_path, monkeypatch, three_people)
    meetings.rename_speaker(m, "S1", "Dana")
    meetings.rename_speaker(m, "S2", "Priya")
    txt = (m.folder / "transcript.txt").read_text()
    assert "Dana: The numbers are in" in txt and "Dana: Hiring is on track" in txt
    assert "Priya: Great, what about hiring" in txt
    assert "Speaker 1" not in txt
    assert "Dana:" in (m.folder / "transcript.srt").read_text()
    assert "**Priya**" in (m.folder / "transcript.md").read_text()


def test_reassign_one_line_to_a_new_person(tmp_path, monkeypatch):
    _settings, m = recorded_meeting(tmp_path, monkeypatch, three_people)
    meetings.rename_speaker(m, "S1", "Dana")
    segments, _ = meetings.load_transcript(m)
    last = next(i for i, s in enumerate(segments) if s.text == "Hiring is on track")
    meetings.reassign_line(m, last, "Sam")
    segments, names = meetings.load_transcript(m)
    assert segments[last].speaker == "Sam"
    assert names["P1"] == "Sam"
    # Assigning to an existing name reuses that person.
    meetings.reassign_line(m, last, "Dana")
    segments, _ = meetings.load_transcript(m)
    assert segments[last].speaker_id == "S1"


def test_regroup_with_a_known_count(tmp_path, monkeypatch):
    _settings, m = recorded_meeting(tmp_path, monkeypatch, three_people)
    meetings.rename_speaker(m, "me", "Joshua")
    meetings.regroup_speakers(m, 1)
    segments, names = meetings.load_transcript(m)
    assert names == {"me": "Joshua", "S1": "Speaker 1"}
    assert {s.speaker for s in segments} == {"Joshua", "Speaker 1"}
    assert "Hiring is on track" in (m.folder / "transcript.txt").read_text()


def test_diarization_failure_keeps_others(tmp_path, monkeypatch):
    def offline(n):
        raise OSError("no network")

    _settings, m = recorded_meeting(tmp_path, monkeypatch, offline)
    assert m.status == meetings.STATUS_DONE
    assert "no network" in m.extra["speaker_error"]
    txt = (m.folder / "transcript.txt").read_text()
    assert "Others: The numbers are in" in txt
