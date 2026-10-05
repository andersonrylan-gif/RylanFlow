"""Offline speaker diarization: after a meeting ends, meetings/session.py uses this to split the
generic "Others" speaker into Speaker 1..N by running sherpa-onnx over the meeting's system.wav.

Needs scripts/fix_sherpa_onnx_dylib.py to have been run once (see docs/decisions/
0004-diarization.md for why) -- sherpa_onnx is imported lazily in _build() so that
importing this module itself never requires that fix to already be in place.
"""

import hashlib
import logging
import shutil
import tarfile
import urllib.request
import wave
from pathlib import Path

import numpy as np

from rylanflow.meetings import speakers
from rylanflow.store import data_dir

log = logging.getLogger(__name__)

SEGMENTATION_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
SEGMENTATION_SHA256 = "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488"

EMBEDDING_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/nemo_en_titanet_small.onnx"
)
EMBEDDING_SHA256 = "ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e"

# The library's own default (0.5) over-segmented a real 9-person meeting into 12 clusters;
# 0.6 matched the true speaker count on that one. But a real 28-minute, multi-speaker meeting
# showed this doesn't generalize: even at 0.8 there was a long tail of 20+ spurious "speakers"
# under a few seconds each, while the real speakers' durations stayed stable across every
# threshold tried. Raised to 0.7 as a moderate improvement, paired with MIN_SPEAKER_DURATION_S
# below to actually clear out that tail regardless of threshold (see docs/decisions/
# 0004-diarization.md).
DEFAULT_CLUSTERING_THRESHOLD = 0.7

# Any speaker whose total talk time across the whole recording is under this gets merged into
# whichever other speaker is temporally nearest (meetings.speakers.merge_short_speakers) -- a
# few seconds of total talk time is almost always a clustering artifact, not a real participant.
MIN_SPEAKER_DURATION_S = 15.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    urllib.request.urlretrieve(url, tmp)  # noqa: S310 - fixed https URL, not user input
    tmp.rename(dest)


def _models_dir() -> Path:
    return data_dir() / "models"


def ensure_segmentation_model() -> Path:
    model_path = _models_dir() / "pyannote-segmentation-3.0" / "model.onnx"
    if model_path.exists():
        return model_path
    archive = _models_dir() / "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
    _download(SEGMENTATION_URL, archive)
    actual = _sha256(archive)
    if actual != SEGMENTATION_SHA256:
        archive.unlink(missing_ok=True)
        raise RuntimeError(f"segmentation model checksum mismatch: got {actual}")
    with tarfile.open(archive, "r:bz2") as tar:
        member = next(
            m for m in tar.getmembers() if m.name.endswith("model.onnx") and "int8" not in m.name
        )
        model_path.parent.mkdir(parents=True, exist_ok=True)
        src = tar.extractfile(member)
        if src is None:
            raise RuntimeError("segmentation archive did not contain model.onnx")
        with src, model_path.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    archive.unlink(missing_ok=True)
    return model_path


def ensure_embedding_model() -> Path:
    model_path = _models_dir() / "nemo_en_titanet_small.onnx"
    if model_path.exists():
        return model_path
    _download(EMBEDDING_URL, model_path)
    actual = _sha256(model_path)
    if actual != EMBEDDING_SHA256:
        model_path.unlink(missing_ok=True)
        raise RuntimeError(f"embedding model checksum mismatch: got {actual}")
    return model_path


def _read_wav_samples(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as f:
        pcm = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def read_wav_window(path: Path, start_s: float, end_s: float) -> np.ndarray:
    """The 16kHz mono float32 samples between start_s and end_s -- used to pull just one
    speaker's audio out of a meeting's WAV for voice-print extraction."""
    with wave.open(str(path), "rb") as f:
        rate = f.getframerate()
        f.setpos(max(0, int(start_s * rate)))
        n_frames = max(0, int((end_s - start_s) * rate))
        pcm = np.frombuffer(f.readframes(n_frames), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


# How much audio (seconds) is enough for a stable voice-print -- more than this just slows
# embedding extraction down for no real gain.
MAX_VOICE_SAMPLE_S = 20.0


def concat_audio_for_turns(
    wav_path: Path, turns: list[tuple[float, float]], max_duration: float = MAX_VOICE_SAMPLE_S
) -> np.ndarray | None:
    """Reads up to `max_duration` seconds of this speaker's audio out of `wav_path`, preferring
    their longest turns first -- a handful of solid turns gives a cleaner voice-print than many
    short, noisy ones. None if `turns` is empty or none of it could be read."""
    if not turns:
        return None
    longest_first = sorted(turns, key=lambda t: t[1] - t[0], reverse=True)
    chunks = []
    total = 0.0
    for start, end in longest_first:
        if total >= max_duration:
            break
        chunk = read_wav_window(wav_path, start, min(end, start + (max_duration - total)))
        if chunk.size:
            chunks.append(chunk)
            total += chunk.size / 16000
    if not chunks:
        return None
    return np.concatenate(chunks)


class Diarizer:
    """Create one (cheap -- no model loading yet), call diarize(wav_path) after a meeting ends.
    The sherpa-onnx pipeline and its models are built lazily on the first call, so constructing
    a Diarizer never triggers a download."""

    def __init__(self, threshold: float = DEFAULT_CLUSTERING_THRESHOLD) -> None:
        self._threshold = threshold
        self._diarization = None
        self._embedding_extractor = None

    def _build_embedding_extractor(self):
        import sherpa_onnx

        embedding_model = ensure_embedding_model()
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(embedding_model))
        return sherpa_onnx.SpeakerEmbeddingExtractor(config)

    def embed(self, samples: np.ndarray) -> list[float] | None:
        """A voice-print for this audio (16kHz mono float32, [-1, 1]), used both to learn a
        newly-named speaker's voice and to recognize them in future meetings (see
        meetings.speakers.match_voice). None if there isn't enough audio to extract one from."""
        if samples.size == 0:
            return None
        if self._embedding_extractor is None:
            self._embedding_extractor = self._build_embedding_extractor()
        stream = self._embedding_extractor.create_stream()
        stream.accept_waveform(sample_rate=16000, waveform=samples)
        stream.input_finished()
        if not self._embedding_extractor.is_ready(stream):
            return None
        return list(self._embedding_extractor.compute(stream))

    def _build(self):
        import sherpa_onnx

        segmentation_model = ensure_segmentation_model()
        embedding_model = ensure_embedding_model()
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(segmentation_model)
                )
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(embedding_model)),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=self._threshold),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        return sherpa_onnx.OfflineSpeakerDiarization(config)

    def diarize(self, wav_path: Path) -> list[tuple[float, float, int]]:
        """(start, end, speaker_index) turns, sorted by start time, with spurious short-lived
        "speakers" already merged into their nearest real neighbor (see
        MIN_SPEAKER_DURATION_S). Speaker indices are 0-based and only meaningful within this
        one call's result."""
        samples = _read_wav_samples(wav_path)
        if samples.size == 0:
            return []
        if self._diarization is None:
            self._diarization = self._build()
        result = self._diarization.process(samples)
        turns = [(seg.start, seg.end, seg.speaker) for seg in result.sort_by_start_time()]
        return speakers.merge_short_speakers(turns, MIN_SPEAKER_DURATION_S)
