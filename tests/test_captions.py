from types import SimpleNamespace

import numpy as np

from meeting_recorder.captions import SAMPLE_RATE, LiveCaptioner

BLOCK = SAMPLE_RATE // 10  # the recorder delivers 100 ms blocks


class FakeModel:
    """Returns a sentence chosen by the loudness of the audio, so tests can tell lines apart."""

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, **_):
        self.calls += 1
        loudest = float(np.abs(audio).max())
        text = "the budget is due friday" if loudest < 0.4 else "sounds good to me"
        return iter([SimpleNamespace(text=" " + text, no_speech_prob=0.05)]), None


def make(captions):
    cap = LiveCaptioner(lambda: None, ("Josh", "Others"), captions.append)
    cap.model = FakeModel()
    return cap


def feed(cap, track, level, seconds):
    for _ in range(int(seconds * 10)):
        cap._segment(track, cap.tracks[track], np.full(BLOCK, level, dtype="float32"))
        while cap._work_once():
            pass


def test_draft_then_final_line():
    out = []
    cap = make(out)
    feed(cap, 1, 0.0005, 1.0)   # background noise
    feed(cap, 1, 0.3, 2.5)      # someone on the call talks
    drafts = [c for c in out if not c.final]
    assert drafts and all(c.speaker == "Others" for c in drafts)
    assert not any(c.final for c in out)
    feed(cap, 1, 0.0005, 1.0)   # they pause
    finals = [c for c in out if c.final]
    assert len(finals) == 1
    assert finals[0].text == "the budget is due friday"
    assert finals[0].id == drafts[0].id  # the final replaces the draft line


def test_long_monologue_is_split():
    out = []
    cap = make(out)
    feed(cap, 0, 0.3, 30)
    finals = [c for c in out if c.final]
    assert len(finals) == 2  # split every 12 seconds
    assert all(c.speaker == "Josh" for c in finals)


def test_mic_echo_of_call_audio_is_dropped():
    out = []
    cap = make(out)
    # The same sentence arrives on the system track and, through the speakers, the mic.
    for _ in range(20):
        for track in (1, 0):
            cap._segment(track, cap.tracks[track], np.full(BLOCK, 0.3, dtype="float32"))
    for _ in range(10):
        for track in (1, 0):
            cap._segment(track, cap.tracks[track], np.zeros(BLOCK, dtype="float32"))
    while cap._work_once():
        pass
    finals = {c.track: c.text for c in out if c.final}
    assert finals[1] == "the budget is due friday"
    assert finals[0] == ""  # the mic copy is blanked, so the window drops it


def test_quiet_room_produces_nothing():
    out = []
    cap = make(out)
    feed(cap, 0, 0.001, 5)
    assert out == []
    assert cap.model.calls == 0
