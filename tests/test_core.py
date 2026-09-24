import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from meeting_recorder import audio, detect, meetings
from meeting_recorder import transcribe as tr
from meeting_recorder.config import Settings


def seg(start, end, speaker, text):
    return tr.Segment(start, end, speaker, text)


def test_merge_joins_same_speaker_and_sorts():
    out = tr.merge_segments([
        seg(5, 6, "Others", "Sounds good."),
        seg(0, 2, "Me", "Hi all."),
        seg(2.5, 4, "Me", "Let's start."),
        seg(10, 11, "Me", "Next item."),
    ])
    assert [(s.speaker, s.text) for s in out] == [
        ("Me", "Hi all. Let's start."),
        ("Others", "Sounds good."),
        ("Me", "Next item."),
    ]


def test_drop_echo_removes_speaker_bleed_only():
    theirs = [seg(10, 13, "Others", "The budget review is on Friday.")]
    mine = [
        seg(10.4, 13.2, "Me", "the budget review is on friday"),  # echo from speakers
        seg(14, 15, "Me", "Friday works for me."),
        seg(40, 42, "Me", "The budget review is on Friday."),  # same words, different time
    ]
    kept = tr.drop_echo(mine, theirs)
    assert [s.start for s in kept] == [14, 40]


def test_export_formats():
    segs = [seg(0, 1.5, "Me", "Hello."), seg(3661.25, 3662, "Others", "Hi.")]
    assert tr.to_text(segs) == "[00:00:00] Me: Hello.\n\n[01:01:01] Others: Hi.\n"
    srt = tr.to_srt(segs)
    assert "1\n00:00:00,000 --> 00:00:01,500\nMe: Hello." in srt
    assert "01:01:01,250 --> 01:01:02,000" in srt
    assert tr.to_markdown(segs, "Sync", "2026-09-24 14:30", 3662).startswith("# Sync\n")


def test_match_meeting_app_skips_self():
    apps = [r"C:\Python312\pythonw.exe", r"C:\Program Files\Zoom\bin\Zoom.exe"]
    assert detect.match_meeting_app(apps) == "Zoom"
    assert detect.match_meeting_app(["MSTeams_8wekyb3d8bbwe"]) == "Microsoft Teams"
    assert detect.match_meeting_app([r"C:\Windows\notepad.exe"]) is None
    assert detect.match_meeting_app([]) is None


def test_settings_roundtrip_ignores_unknown_keys(tmp_path):
    path = tmp_path / "s.json"
    Settings(your_name="Josh", whisper_model="medium").save(path)
    raw = json.loads(path.read_text())
    raw["old_setting"] = 1
    path.write_text(json.dumps(raw))
    s = Settings.load(path)
    assert (s.your_name, s.whisper_model) == ("Josh", "medium")


def test_safe_name():
    assert meetings.safe_name('Q3: plan / "draft"?') == "Q3 plan  draft"
    assert meetings.safe_name("...") == "Meeting"


def test_pad_to_clock_fills_gaps(tmp_path, monkeypatch):
    sr = 16000
    monkeypatch.setattr(audio.time, "monotonic", lambda: 100.0)
    with sf.SoundFile(tmp_path / "a.wav", "w", samplerate=sr, channels=1) as out:
        # 2 s have passed and nothing has been written: expect ~2 s minus the incoming chunk.
        padded = audio._pad_to_clock(out, 0, 1600, t0=98.0, sr=sr)
        assert padded == 2 * sr - 1600
        # On time: no padding.
        assert audio._pad_to_clock(out, 2 * sr - 1600, 1600, t0=98.0, sr=sr) == 0


def test_mix_tracks_handles_unequal_lengths(tmp_path):
    sr = 16000
    sf.write(tmp_path / "mic.wav", np.full(sr, 0.25, dtype="float32"), sr)
    sf.write(tmp_path / "system.wav", np.full(sr * 2, 0.5, dtype="float32"), sr)
    audio.mix_tracks(tmp_path / "mic.wav", tmp_path / "system.wav", tmp_path / "m.wav", block=5000)
    mixed, _ = sf.read(tmp_path / "m.wav")
    assert len(mixed) == sr * 2
    assert abs(mixed[100] - 0.75) < 1e-3
    assert abs(mixed[-100] - 0.5) < 1e-3


class FakeModel:
    """Stands in for faster-whisper: returns canned segments per file name."""

    def __init__(self, by_file):
        self.by_file = by_file

    def transcribe(self, path, **_):
        segs = [SimpleNamespace(start=s, end=e, text=t) for s, e, t in self.by_file[Path(path).name]]
        return iter(segs), SimpleNamespace(duration=60.0)


def test_process_writes_all_outputs(tmp_path):
    settings = Settings(output_dir=str(tmp_path), your_name="Josh", diarize=False)
    m = meetings.new_meeting(settings, "Weekly sync")
    sr = 16000
    sf.write(m.folder / "mic.wav", np.zeros(sr * 2, dtype="float32"), sr)
    sf.write(m.folder / "system.wav", np.zeros(sr * 2, dtype="float32"), sr)
    model = FakeModel({
        "mic.wav": [(0.0, 1.0, " Morning everyone."), (5.0, 6.0, " Thanks, see you.")],
        "system.wav": [(1.5, 4.0, " Morning! Quick update from me.")],
    })
    calls = []
    done = meetings.process(m, settings, model, progress=lambda f, w: calls.append(f))

    assert done.status == meetings.STATUS_DONE, done.error
    assert calls[-1] == 1.0
    txt = (m.folder / "transcript.txt").read_text()
    assert txt.splitlines()[0] == "[00:00:00] Josh: Morning everyone."
    assert "Others: Morning! Quick update from me." in txt
    for name in ("transcript.md", "transcript.srt", "transcript.json", "meeting.wav"):
        assert (m.folder / name).exists()
    listed = meetings.list_meetings(settings)
    assert [x.title for x in listed] == ["Weekly sync"]
    assert listed[0].status == meetings.STATUS_DONE


def test_process_records_errors(tmp_path):
    settings = Settings(output_dir=str(tmp_path))
    m = meetings.new_meeting(settings, "")
    sf.write(m.folder / "mic.wav", np.zeros(16000, dtype="float32"), 16000)

    class Broken:
        def transcribe(self, *a, **k):
            raise RuntimeError("boom")

    done = meetings.process(m, settings, Broken())
    assert done.status == meetings.STATUS_ERROR
    assert "boom" in done.error
    assert meetings.Meeting.load(m.folder).status == meetings.STATUS_ERROR


def test_automatic_behaviors_default_on_and_can_be_turned_off(tmp_path):
    s = Settings()
    assert s.consent_reminder and s.close_to_tray and s.auto_detect and s.auto_stop and s.auto_process
    path = tmp_path / "s.json"
    Settings(consent_reminder=False, close_to_tray=False).save(path)
    loaded = Settings.load(path)
    assert not loaded.consent_reminder and not loaded.close_to_tray


def test_startup_command_launches_minimized():
    from meeting_recorder import startup

    assert startup.command().endswith("--minimized")
