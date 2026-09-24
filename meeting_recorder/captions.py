"""Live captions while recording.

Each track (0 = your mic, 1 = system audio) is cut into utterances by a simple
loudness detector: speech starts when a 100 ms block is clearly louder than the
background, and ends after a pause. While someone is talking, the growing utterance
is re-transcribed about once a second and shown as a draft line. When they pause,
the utterance is transcribed once more and the line becomes final.

This uses a small Whisper model so it keeps up on a laptop CPU. The saved transcript
still comes from the accurate pass after the meeting, so captions never affect it.
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .transcribe import _similar

SAMPLE_RATE = 16000
PRE_ROLL_S = 0.3          # keep a little audio from before speech starts
END_SILENCE_S = 0.7       # a pause this long ends the utterance
MAX_UTTERANCE_S = 12.0    # force a line break in long monologues
DRAFT_EVERY_S = 1.0       # how often to refresh the draft line
MIN_DRAFT_S = 0.6         # don't transcribe tiny fragments
MIN_THRESHOLD = 0.006     # RMS floor for "speech", on a -1..1 scale


@dataclass
class Caption:
    id: int
    track: int
    speaker: str
    text: str
    final: bool
    started: float


@dataclass
class _TrackState:
    speaker: str
    noise: float = 0.01
    in_speech: bool = False
    silence: float = 0.0
    chunks: list = field(default_factory=list)
    pre_roll: list = field(default_factory=list)
    caption_id: int | None = None
    started: float = 0.0
    drafted_len: int = 0

    def samples(self) -> int:
        return sum(len(c) for c in self.chunks)


class LiveCaptioner(threading.Thread):
    """Feed it audio with feed(); it calls on_caption from its own thread."""

    def __init__(self, model_loader: Callable[[], object], speakers: tuple[str, str],
                 on_caption: Callable[[Caption], None], language: str | None = None,
                 on_status: Callable[[str], None] | None = None):
        super().__init__(daemon=True, name="captions")
        self.model_loader = model_loader
        self.on_caption = on_caption
        self.on_status = on_status or (lambda _msg: None)
        self.language = language
        self.tracks = [_TrackState(speakers[0]), _TrackState(speakers[1])]
        self._inbox: queue.Queue = queue.Queue()
        self._finals: list[tuple[_TrackState, np.ndarray, int, float]] = []
        self._stop = threading.Event()
        self._ids = itertools.count(1)
        self._recent_other: list[tuple[float, str]] = []  # (time, text) for echo checks
        self.model = None

    # Called from the recording threads. Must stay cheap.
    def feed(self, track: int, samples: np.ndarray) -> None:
        self._inbox.put((track, samples.copy()))

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            self.on_status("Loading caption model...")
            self.model = self.model_loader()
            self.on_status("")
        except Exception as exc:
            self.on_status(f"Captions unavailable: {exc}")
            return
        while not self._stop.is_set():
            self._drain()
            if not self._work_once():
                time.sleep(0.05)

    # ---- segmentation --------------------------------------------------------------

    def _drain(self) -> None:
        while True:
            try:
                track, samples = self._inbox.get_nowait()
            except queue.Empty:
                return
            self._segment(track, self.tracks[track], samples)

    def _segment(self, index: int, st: _TrackState, block: np.ndarray) -> None:
        if len(block) == 0:
            return
        seconds = len(block) / SAMPLE_RATE
        rms = float(np.sqrt(np.mean(block ** 2)))
        threshold = max(MIN_THRESHOLD, st.noise * 3)
        loud = rms > threshold
        if not st.in_speech:
            # Track the background level so a noisy room doesn't count as speech.
            st.noise = 0.95 * st.noise + 0.05 * rms if not loud else st.noise
            st.pre_roll.append(block)
            while sum(len(c) for c in st.pre_roll) > PRE_ROLL_S * SAMPLE_RATE:
                st.pre_roll.pop(0)
            if loud:
                st.in_speech = True
                st.silence = 0.0
                st.chunks = list(st.pre_roll)
                st.pre_roll = []
                st.caption_id = next(self._ids)
                st.started = time.time()
                st.drafted_len = 0
            return
        st.chunks.append(block)
        st.silence = 0.0 if loud else st.silence + seconds
        if st.silence >= END_SILENCE_S or st.samples() >= MAX_UTTERANCE_S * SAMPLE_RATE:
            audio = np.concatenate(st.chunks)
            self._finals.append((st, audio, st.caption_id, st.started))
            st.in_speech = False
            st.chunks = []
            st.caption_id = None

    # ---- transcription ---------------------------------------------------------------

    def _work_once(self) -> bool:
        """Do one transcription if any is due. Finished lines go first."""
        if self._finals:
            # Call-audio lines first, so a mic line that echoes one can be recognized.
            pick = next((i for i, f in enumerate(self._finals) if f[0] is self.tracks[1]), 0)
            st, audio, cid, started = self._finals.pop(pick)
            text = self._transcribe(audio)
            track = self.tracks.index(st)
            if track == 0 and self._is_echo(text):
                text = ""  # the mic picked up the call audio from speakers
            if track == 1 and text:
                self._recent_other.append((time.time(), text))
            self.on_caption(Caption(cid, track, st.speaker, text, True, started))
            return True
        for track, st in enumerate(self.tracks):
            n = st.samples()
            if (st.in_speech and n >= MIN_DRAFT_S * SAMPLE_RATE
                    and n - st.drafted_len >= DRAFT_EVERY_S * SAMPLE_RATE):
                st.drafted_len = n
                text = self._transcribe(np.concatenate(st.chunks))
                if text and st.caption_id is not None:
                    self.on_caption(Caption(st.caption_id, track, st.speaker, text, False, st.started))
                return True
        return False

    def _transcribe(self, audio: np.ndarray) -> str:
        segments, _info = self.model.transcribe(
            audio.astype("float32"),
            language=self.language,
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        # Whisper invents text ("Thank you.") for noise. Drop segments it isn't
        # confident contain speech.
        parts = [s.text.strip() for s in segments if getattr(s, "no_speech_prob", 0.0) < 0.6]
        return " ".join(p for p in parts if p)

    def _is_echo(self, text: str) -> bool:
        if not text:
            return False
        cutoff = time.time() - 8
        self._recent_other = [(t, x) for t, x in self._recent_other if t >= cutoff]
        return any(_similar(text, other) >= 0.6 for _t, other in self._recent_other)
