"""Tell apart the different people on the call audio ("who spoke when").

Uses sherpa-onnx with two small ONNX models: pyannote's segmentation model finds
where each voice speaks, and a speaker-embedding model turns each stretch of speech
into a voiceprint so matching voices can be grouped. Everything runs locally. The
models (about 47 MB) download once, the first time a meeting is processed.
"""

from __future__ import annotations

import os
import ssl
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from .config import settings_dir

RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION_URL = f"{RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
SEGMENTATION_FILE = "sherpa-onnx-pyannote-segmentation-3-0/model.onnx"
# The embedding model sherpa-onnx's own diarization examples use. In testing it
# separated voices that the English VoxCeleb ResNet34 model merged.
EMBEDDING_URL = f"{RELEASES}/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
EMBEDDING_FILE = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
# Distance threshold for grouping voices when the number of speakers isn't known.
# Lower splits more readily.
CLUSTER_THRESHOLD = 0.5


@dataclass
class Turn:
    start: float
    end: float
    speaker: int  # 0-based, numbered in order of first appearance


def models_dir() -> Path:
    return settings_dir() / "models" / "diarization"


def _ssl_context() -> ssl.SSLContext:
    """Certificates for the model download.

    Prefer the operating system's store, which includes company proxy certificates
    that IT installs. Honor SSL_CERT_FILE / REQUESTS_CA_BUNDLE if set. Fall back to
    certifi only when the system store is empty, as in a packaged macOS app.
    """
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        path = os.environ.get(var)
        if path and os.path.exists(path):
            return ssl.create_default_context(cafile=path)
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) == 0:
        try:
            import certifi

            ctx.load_verify_locations(certifi.where())
        except ImportError:
            pass
    return ctx


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, context=_ssl_context(), timeout=60) as resp, open(tmp, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)
    os.replace(tmp, dest)


def ensure_models(status: Callable[[str], None] | None = None) -> tuple[Path, Path]:
    root = models_dir()
    root.mkdir(parents=True, exist_ok=True)
    segmentation = root / SEGMENTATION_FILE
    embedding = root / EMBEDDING_FILE
    if not segmentation.exists():
        if status:
            status("Downloading the speaker-separation models (first time only)...")
        archive = root / "segmentation.tar.bz2"
        _download(SEGMENTATION_URL, archive)
        with tarfile.open(archive) as tar:
            try:
                tar.extractall(root, filter="data")
            except TypeError:  # Python without extraction filters (before 3.10.12)
                tar.extractall(root)
        archive.unlink()
    if not embedding.exists():
        if status:
            status("Downloading the speaker-separation models (first time only)...")
        _download(EMBEDDING_URL, embedding)
    return segmentation, embedding


def diarize(path: Path, num_speakers: int | None = None,
            status: Callable[[str], None] | None = None) -> list[Turn]:
    """Returns who spoke when in a 16 kHz mono recording. num_speakers=None guesses."""
    audio, sr = sf.read(str(path), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        n = int(len(audio) * 16000 / sr)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype("float32")
    if len(audio) < 16000:
        return []

    segmentation, embedding = ensure_models(status)
    if status:
        status("Telling speakers apart...")
    turns = _run(audio, segmentation, embedding, num_speakers)
    if num_speakers and len({t.speaker for t in turns}) < num_speakers:
        # With a fixed count, sherpa-onnx sometimes spends a cluster on fragments
        # that get filtered out, leaving one person short. One step up fixes that.
        retry = _run(audio, segmentation, embedding, num_speakers + 1)
        if len({t.speaker for t in retry}) == num_speakers:
            turns = retry
    return renumber(turns)


def _run(audio: np.ndarray, segmentation: Path, embedding: Path,
         num_speakers: int | None) -> list[Turn]:
    import sherpa_onnx

    threads = max(1, min(8, (os.cpu_count() or 2) - 1))
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentation)),
            num_threads=threads,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(embedding), num_threads=threads),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=num_speakers if num_speakers else -1,
            threshold=CLUSTER_THRESHOLD,
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("Speaker-separation models failed to load")
    result = sherpa_onnx.OfflineSpeakerDiarization(config).process(audio).sort_by_start_time()
    return [Turn(r.start, r.end, r.speaker) for r in result]


def renumber(turns: list[Turn]) -> list[Turn]:
    """Number speakers in the order they first talk, so "Speaker 1" speaks first."""
    order: dict[int, int] = {}
    for t in sorted(turns, key=lambda t: t.start):
        order.setdefault(t.speaker, len(order))
    return [Turn(t.start, t.end, order[t.speaker]) for t in sorted(turns, key=lambda t: t.start)]
