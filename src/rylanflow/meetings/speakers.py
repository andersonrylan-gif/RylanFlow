"""Pure logic for cleaning up what the mic heard: dropping silence, and dropping mic segments
that just picked up the system audio (the other participants) because the user isn't wearing
headphones. (Diarization -- splitting system audio into Speaker 1, 2, ... -- is step 3.5; this
module only handles the mic side.)
"""

import difflib

import numpy as np

ECHO_OVERLAP_FRACTION = 0.5  # how much of the mic segment must overlap a system one
ECHO_TEXT_SIMILARITY = 0.6  # how similar the words have to be, 0..1 (difflib ratio)
SILENCE_RMS_THRESHOLD = 0.01  # mic audio quieter than this isn't worth transcribing at all


def is_silent(audio: np.ndarray, threshold: float = SILENCE_RMS_THRESHOLD) -> bool:
    if audio.size == 0:
        return True
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    return rms < threshold


def _overlap_fraction(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """How much of segment A's duration overlaps segment B, as a fraction of A's length."""
    a_length = a_end - a_start
    if a_length <= 0:
        return 0.0
    overlap = min(a_end, b_end) - max(a_start, b_start)
    return max(0.0, overlap) / a_length


def is_echo(
    mic_text: str,
    mic_start: float,
    mic_end: float,
    system_segments: list[tuple[float, float, str]],
) -> bool:
    """True if a mic segment looks like it's just the mic picking up the system audio (the
    other participants) rather than the user's own voice. `system_segments` is
    (start, end, text) triples, already offset to the same absolute meeting timeline as
    mic_start/mic_end."""
    for system_start, system_end, system_text in system_segments:
        if _overlap_fraction(mic_start, mic_end, system_start, system_end) < ECHO_OVERLAP_FRACTION:
            continue
        similarity = difflib.SequenceMatcher(None, mic_text.lower(), system_text.lower()).ratio()
        if similarity >= ECHO_TEXT_SIMILARITY:
            return True
    return False
