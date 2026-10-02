"""Cuts a continuous audio stream into ~30s windows at quiet points, for incremental
transcription during a meeting. Pure logic: no audio hardware, no AppKit, fully unit-testable.
"""

import numpy as np

DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_TARGET_S = 30.0
DEFAULT_MAX_S = 40.0
DEFAULT_MIN_SILENCE_S = 0.4
# An RMS below this counts as "quiet enough to cut here" -- real speech runs well above it (seen
# ~0.02 average, peaks to ~0.25 against a live mic); ambient room noise and pauses sit near 0.
DEFAULT_SILENCE_RMS_THRESHOLD = 0.01
_SEARCH_STEP_DIVISOR = 4  # how finely to scan for the quietest point, relative to the window


class Chunker:
    """Feed it audio with push(); it hands back windows once there's enough buffered, cut as
    close to a natural pause as it can find. Offsets are absolute seconds from the first call,
    not relative to any one push()."""

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        target_s: float = DEFAULT_TARGET_S,
        max_s: float = DEFAULT_MAX_S,
        min_silence_s: float = DEFAULT_MIN_SILENCE_S,
        silence_rms_threshold: float = DEFAULT_SILENCE_RMS_THRESHOLD,
    ) -> None:
        self.sample_rate = sample_rate
        self.target_s = target_s
        self.max_s = max_s
        self.min_silence_s = min_silence_s
        self.silence_rms_threshold = silence_rms_threshold
        self._buffer = np.zeros(0, dtype=np.float32)
        self._buffer_start_s = 0.0

    def push(self, audio: np.ndarray) -> list[tuple[float, np.ndarray]]:
        """Feed in newly captured audio. Returns zero or more (offset_seconds, audio) windows
        -- zero, almost always; more than one only if a lot of audio arrived in one call."""
        self._buffer = np.concatenate([self._buffer, audio]) if self._buffer.size else audio.copy()
        windows = []
        while self._buffer.size / self.sample_rate >= self.max_s:
            windows.append(self._cut())
        return windows

    def flush(self) -> list[tuple[float, np.ndarray]]:
        """Call when the meeting ends: returns whatever's left, however short."""
        if self._buffer.size == 0:
            return []
        window = (self._buffer_start_s, self._buffer)
        self._buffer = np.zeros(0, dtype=np.float32)
        self._buffer_start_s += window[1].size / self.sample_rate
        return [window]

    def _cut(self) -> tuple[float, np.ndarray]:
        cut_sample = self._find_quietest_cut()
        offset = self._buffer_start_s
        chunk = self._buffer[:cut_sample]
        self._buffer = self._buffer[cut_sample:]
        self._buffer_start_s += cut_sample / self.sample_rate
        return offset, chunk

    def _find_quietest_cut(self) -> int:
        """A sample index to cut at, searching [target_s, max_s] for the quietest
        min_silence_s-long stretch. Falls back to a hard cut at max_s if that range is too
        short to search, or if nothing is particularly quiet in it."""
        search_start = int(self.target_s * self.sample_rate)
        search_end = int(self.max_s * self.sample_rate)
        window = int(self.min_silence_s * self.sample_rate)
        if window <= 0 or search_end - search_start < window:
            return min(search_end, self._buffer.size)

        step = max(1, window // _SEARCH_STEP_DIVISOR)
        best_start, best_rms = None, np.inf
        for i in range(search_start, search_end - window + 1, step):
            segment = self._buffer[i : i + window]
            rms = float(np.sqrt(np.mean(segment.astype(np.float64) ** 2)))
            if rms < best_rms:
                best_rms = rms
                best_start = i
        if best_start is not None and best_rms <= self.silence_rms_threshold:
            # Cutting at the start of the quietest stretch keeps that silence with the *next*
            # chunk, so the next chunk doesn't open mid-word.
            return best_start
        # Nothing in the search window was actually quiet (e.g. continuous speech) -- rather
        # than cut arbitrarily mid-sentence near target_s, take the longest chunk we're allowed.
        return min(search_end, self._buffer.size)
