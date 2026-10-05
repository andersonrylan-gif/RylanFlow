"""Continuous microphone capture for a meeting -- runs for the whole meeting rather than
start/stop per dictation, and opens its own stream so dictation (recorder.Recorder) can keep
working at the same time.

Follows the same safety pattern as recorder.Recorder: stopping a PortAudio stream can deadlock
inside CoreAudio, so close() never waits for it; audio captured up to that point is kept either
way, both in memory (for drain()) and already written to disk.
"""

import logging
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

from rylanflow.recorder import (
    CLOSE_TIMEOUT,
    OPEN_TIMEOUT,
    SAMPLE_RATE,
    IncrementalWavWriter,
    run_with_timeout,
)

log = logging.getLogger(__name__)

# Opening the mic right as a call starts can race another app's virtual audio device
# reconfiguring the input graph (e.g. Krisp's noise-cancelling mic) -- PortAudio surfaces that
# as a generic "Internal PortAudio error" rather than anything retry-aware itself. A short
# retry gives that race a chance to clear instead of failing the whole meeting outright.
MIC_OPEN_RETRIES = 3
MIC_OPEN_RETRY_DELAY = 0.5


class MicTrack:
    """Captures the user's own voice continuously for the length of a meeting."""

    def __init__(
        self, wav_path: str | Path, sample_rate: int = SAMPLE_RATE, clock=time.monotonic
    ) -> None:
        self.sample_rate = sample_rate
        self._clock = clock
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._closing: list[tuple[threading.Thread, float]] = []
        self._writer = IncrementalWavWriter(wav_path, sample_rate)

    @property
    def stuck(self) -> bool:
        now = self._clock()
        self._closing = [(t, since) for t, since in self._closing if t.is_alive()]
        return any(now - since > CLOSE_TIMEOUT for _, since in self._closing)

    def start(self) -> None:
        if self._stream is not None:
            return
        self._stream = run_with_timeout(self._open, OPEN_TIMEOUT)

    def drain(self) -> np.ndarray:
        """Returns and clears whatever's been captured since the last drain()."""
        with self._lock:
            chunks, self._chunks = self._chunks, []
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)

    def close(self) -> None:
        """Non-blocking, like Recorder.stop(). The WAV file is closed once the stream actually
        finishes closing, on a background thread."""
        stream, self._stream = self._stream, None
        if stream is None:
            self._writer.close()
            return
        thread = threading.Thread(
            target=self._finish_close, args=(stream,), name="mic-track-close", daemon=True
        )
        thread.start()
        self._closing.append((thread, self._clock()))

    def _finish_close(self, stream: sd.InputStream) -> None:
        try:
            stream.abort()
            stream.close()
        except Exception:
            log.exception("closing the mic track stream failed")
        finally:
            self._writer.close()

    def _on_audio(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        block = indata[:, 0].copy()
        with self._lock:
            self._chunks.append(block)
        try:
            self._writer.append(block)
        except Exception:
            log.exception("writing mic track audio to disk failed")

    def _open(self) -> sd.InputStream:
        for attempt in range(1, MIC_OPEN_RETRIES + 1):
            try:
                stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=self._on_audio,
                )
                stream.start()
                return stream
            except sd.PortAudioError:
                if attempt == MIC_OPEN_RETRIES:
                    raise
                log.warning(
                    "mic track open attempt %d/%d failed, retrying", attempt, MIC_OPEN_RETRIES
                )
                time.sleep(MIC_OPEN_RETRY_DELAY)
