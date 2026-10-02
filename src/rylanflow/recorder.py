"""Microphone recording: stream audio into a buffer, return a numpy array."""

import logging
import os
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000  # Whisper's native rate
CHANNELS = 1
OPEN_TIMEOUT = 5.0  # seconds to wait for the mic to start
CLOSE_TIMEOUT = 3.0  # a stream still closing after this long is stuck


class AudioStuckError(RuntimeError):
    """CoreAudio stopped responding; only a restart of the process recovers it."""


def _run_with_timeout(fn, timeout: float):
    """Run fn on a helper thread. Returns its result, or raises AudioStuckError on timeout."""
    result: dict = {}

    def target() -> None:
        try:
            result["value"] = fn()
        except BaseException as exc:  # re-raised on the caller's thread
            result["error"] = exc

    thread = threading.Thread(target=target, name="mic-open", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise AudioStuckError("microphone did not start in time")
    if "error" in result:
        raise result["error"]
    return result.get("value")


class Recorder:
    """Records mono 16 kHz audio between start() and stop().

    Stopping a PortAudio stream on macOS can deadlock inside CoreAudio, so stop() never waits
    for it: it returns the captured audio at once and closes the stream on a background thread.
    If that close hangs, `stuck` becomes True and the app restarts itself.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, clock=time.monotonic) -> None:
        self.sample_rate = sample_rate
        self._clock = clock
        self._chunks: list[np.ndarray] = []
        self._capturing = False
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._closing: list[tuple[threading.Thread, float]] = []

    @property
    def recording(self) -> bool:
        return self._stream is not None

    @property
    def stuck(self) -> bool:
        """True if an old stream has been trying to close for longer than CLOSE_TIMEOUT."""
        now = self._clock()
        self._closing = [(t, since) for t, since in self._closing if t.is_alive()]
        return any(now - since > CLOSE_TIMEOUT for _, since in self._closing)

    def _on_audio(self, indata: np.ndarray, frames: int, time, status) -> None:
        with self._lock:
            if self._capturing:
                self._chunks.append(indata[:, 0].copy())

    def _open(self) -> sd.InputStream:
        stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=CHANNELS,
            dtype="float32",
            callback=self._on_audio,
        )
        stream.start()
        return stream

    def start(self) -> None:
        if self._stream is not None:
            return
        if self.stuck:
            raise AudioStuckError("previous microphone stream never closed")
        with self._lock:
            self._chunks = []
            self._capturing = True
        try:
            self._stream = _run_with_timeout(self._open, OPEN_TIMEOUT)
        except BaseException:
            with self._lock:
                self._capturing = False
            raise

    def stop(self) -> np.ndarray:
        """Stop recording and return the audio as a float32 array in [-1, 1]. Never blocks."""
        with self._lock:
            self._capturing = False
            chunks, self._chunks = self._chunks, []
        stream, self._stream = self._stream, None
        if stream is not None:
            self._close_in_background(stream)
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    def _close_in_background(self, stream) -> None:
        def close() -> None:
            if os.environ.get("RYLANFLOW_DEBUG_HANG_ON_CLOSE"):  # for testing recovery
                threading.Event().wait()
            try:
                stream.abort()  # don't wait for buffers to drain
                stream.close()
            except Exception:
                log.exception("closing the microphone stream failed")

        thread = threading.Thread(target=close, name="mic-close", daemon=True)
        thread.start()
        self._closing.append((thread, self._clock()))


def write_wav(path: str | Path, audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    """Save a float32 mono array as 16-bit PCM WAV."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(CHANNELS)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(pcm.tobytes())
