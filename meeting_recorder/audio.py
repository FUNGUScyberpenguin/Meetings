"""Record the microphone and the system audio (WASAPI loopback) as two aligned WAV tracks.

Two separate tracks let the transcript tell "you" (mic) apart from everyone else on
the call (system audio) without any speaker-identification model.
"""

from __future__ import annotations

import sys
import threading
import time
import warnings
from pathlib import Path

import numpy as np
import soundfile as sf

CHUNK_SECONDS = 0.1
# If a track falls this far behind the wall clock, fill the gap with silence so the
# mic and system tracks stay lined up.
MAX_LAG_SECONDS = 0.25


def _soundcard():
    # Imported lazily so the rest of the app (and the tests) work without audio devices.
    import soundcard

    warnings.filterwarnings("ignore", category=soundcard.SoundcardRuntimeWarning)
    return soundcard


def _init_com_for_thread() -> None:
    # soundcard initializes COM on the thread that imports it. Worker threads need
    # their own init or WASAPI calls fail with CO_E_NOTINITIALIZED.
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED


def list_devices() -> dict[str, list[str]]:
    sc = _soundcard()
    return {
        "microphones": [m.name for m in sc.all_microphones()],
        "speakers": [s.name for s in sc.all_speakers()],
    }


def find_microphone(name: str | None):
    sc = _soundcard()
    return sc.get_microphone(name) if name else sc.default_microphone()


def find_loopback(speaker_name: str | None):
    """Return a loopback 'microphone' that captures what the given speaker plays."""
    sc = _soundcard()
    speaker = sc.get_speaker(speaker_name) if speaker_name else sc.default_speaker()
    return sc.get_microphone(speaker.name, include_loopback=True), speaker


class _TrackThread(threading.Thread):
    def __init__(self, device, path: Path, samplerate: int, t0: float, stop: threading.Event):
        super().__init__(daemon=True, name=f"track-{path.stem}")
        self.device = device
        self.path = path
        self.samplerate = samplerate
        self.t0 = t0
        self.stop_event = stop
        self.level = 0.0
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            _init_com_for_thread()
            sr = self.samplerate
            block = int(sr * CHUNK_SECONDS)
            written = 0
            with self.device.recorder(samplerate=sr, channels=1) as rec, sf.SoundFile(
                self.path, "w", samplerate=sr, channels=1, subtype="PCM_16"
            ) as out:
                while not self.stop_event.is_set():
                    data = rec.record(numframes=block)
                    mono = data.mean(axis=1) if data.ndim > 1 else data
                    written += _pad_to_clock(out, written, len(mono), self.t0, sr)
                    out.write(mono)
                    written += len(mono)
                    self.level = float(np.sqrt(np.mean(mono**2))) if len(mono) else 0.0
        except Exception as exc:  # surfaced to the UI through Recorder.errors
            self.error = exc


def _pad_to_clock(out, written: int, incoming: int, t0: float, sr: int) -> int:
    """Write silence if the track has fallen behind real time. Returns frames written."""
    expected = int((time.monotonic() - t0) * sr) - incoming
    gap = expected - written
    if gap > MAX_LAG_SECONDS * sr:
        out.write(np.zeros(gap, dtype="float32"))
        return gap
    return 0


class _SilencePlayer(threading.Thread):
    """Plays silence on the output device.

    Some drivers deliver no loopback data at all while nothing is playing. A silent
    stream keeps the loopback capture ticking so timestamps stay accurate.
    """

    def __init__(self, speaker, samplerate: int, stop: threading.Event):
        super().__init__(daemon=True, name="silence")
        self.speaker = speaker
        self.samplerate = samplerate
        self.stop_event = stop

    def run(self) -> None:
        try:
            _init_com_for_thread()
            chunk = np.zeros((int(self.samplerate * CHUNK_SECONDS), 1), dtype="float32")
            with self.speaker.player(samplerate=self.samplerate, channels=1) as player:
                while not self.stop_event.is_set():
                    player.play(chunk)
        except Exception:
            pass  # optional helper; recording still works without it on most drivers


class Recorder:
    """Records mic.wav and system.wav into a folder until stop() is called."""

    def __init__(self, folder: Path, samplerate: int = 16000,
                 mic_name: str | None = None, speaker_name: str | None = None):
        self.folder = folder
        self.samplerate = samplerate
        self.mic_name = mic_name
        self.speaker_name = speaker_name
        self._stop = threading.Event()
        self._threads: list[_TrackThread] = []
        self._silence: _SilencePlayer | None = None
        self.started_at: float | None = None

    @property
    def mic_path(self) -> Path:
        return self.folder / "mic.wav"

    @property
    def system_path(self) -> Path:
        return self.folder / "system.wav"

    def start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        mic = find_microphone(self.mic_name)
        loopback, speaker = find_loopback(self.speaker_name)
        t0 = time.monotonic()
        self.started_at = time.time()
        self._silence = _SilencePlayer(speaker, 48000, self._stop)
        self._silence.start()
        self._threads = [
            _TrackThread(mic, self.mic_path, self.samplerate, t0, self._stop),
            _TrackThread(loopback, self.system_path, self.samplerate, t0, self._stop),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        if self._silence:
            self._silence.join(timeout=2)

    @property
    def levels(self) -> tuple[float, float]:
        if len(self._threads) != 2:
            return 0.0, 0.0
        return self._threads[0].level, self._threads[1].level

    @property
    def errors(self) -> list[str]:
        names = ("Microphone", "System audio")
        return [f"{n}: {t.error}" for n, t in zip(names, self._threads) if t.error]

    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at if self.started_at else 0.0


def mix_tracks(a: Path, b: Path, out_path: Path, block: int = 16000 * 30) -> None:
    """Mix two mono WAVs into one file for playback, streaming in blocks to keep memory flat."""
    have = [p for p in (a, b) if p.exists()]
    if not have:
        return
    infos = [sf.info(str(p)) for p in have]
    sr = infos[0].samplerate
    total = max(i.frames for i in infos)
    readers = [sf.SoundFile(str(p)) for p in have]
    try:
        with sf.SoundFile(out_path, "w", samplerate=sr, channels=1, subtype="PCM_16") as out:
            done = 0
            while done < total:
                n = min(block, total - done)
                acc = np.zeros(n, dtype="float32")
                for r in readers:
                    chunk = r.read(n, dtype="float32")
                    if chunk.ndim > 1:
                        chunk = chunk.mean(axis=1)
                    acc[: len(chunk)] += chunk
                out.write(np.clip(acc, -1.0, 1.0))
                done += n
    finally:
        for r in readers:
            r.close()
