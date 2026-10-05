"""Pure logic for cleaning up what the mic heard: dropping silence, and dropping mic segments
that just picked up the system audio (the other participants) because the user isn't wearing
headphones. Also the pure half of diarization (step 3.5) -- assigning each system-track segment
to a speaker turn; the impure half (actually running a diarization model over system.wav to
produce those turns) lives in meetings/diarize.py.
"""

import difflib
import math

import numpy as np

ECHO_OVERLAP_FRACTION = 0.5  # how much of the mic segment must overlap a system one
ECHO_TEXT_SIMILARITY = 0.6  # how similar the words have to be, 0..1 (difflib ratio)
SILENCE_RMS_THRESHOLD = 0.01  # mic audio quieter than this isn't worth transcribing at all

# Measured against real meeting audio (two confirmed-same-person windows, two confirmed-
# different-person windows): same-speaker similarity landed around 0.44-0.66, different-speaker
# around 0.04-0.24. Set conservatively high within that gap -- a missed match just falls back to
# "Speaker N" (safe), while a wrong match would mislabel a stranger with someone else's name.
VOICE_MATCH_THRESHOLD = 0.5


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


def merge_short_speakers(
    turns: list[tuple[float, float, int]], min_total_duration: float
) -> list[tuple[float, float, int]]:
    """Merges any speaker whose total talk time across the whole recording is under
    `min_total_duration` into whichever other (non-short) speaker is temporally nearest to each
    of its turns. A few seconds of total talk time is almost always a clustering artifact (a
    brief, hard-to-embed segment scattering into its own spurious cluster) rather than a
    genuinely distinct person -- confirmed against a real 28-minute, multi-speaker meeting: even
    at the clustering library's strictest reasonable threshold, the long tail of sub-few-second
    "speakers" persisted while the real speakers' durations stayed stable across every
    threshold. Returns a new list in the same (start, end, speaker_index) shape; only speaker
    indices are ever changed, never the timing."""
    if not turns:
        return []
    total_duration: dict[int, float] = {}
    for start, end, speaker in turns:
        total_duration[speaker] = total_duration.get(speaker, 0.0) + (end - start)

    real_speakers = {s for s, duration in total_duration.items() if duration >= min_total_duration}
    if not real_speakers:
        # Everything is "short" (e.g. a very brief recording) -- nothing to merge into.
        return turns

    merged = []
    for start, end, speaker in turns:
        if speaker in real_speakers:
            merged.append((start, end, speaker))
            continue
        center = (start + end) / 2
        nearest = min(
            (t for t in turns if t[2] in real_speakers),
            key=lambda t: abs((t[0] + t[1]) / 2 - center),
        )
        merged.append((start, end, nearest[2]))
    return merged


def assign(segments: list[dict], turns: list[tuple[float, float, int]]) -> dict[int, int]:
    """Maps each segment's id to a 0-based speaker index: whichever diarization turn overlaps
    it the most, in seconds. If nothing overlaps at all (or two turns tie exactly), the nearest
    turn by center-to-center distance wins. `turns` is (start, end, speaker_index) triples from
    meetings/diarize.py, `segments` are dicts with at least id/start_s/end_s. Returns {} if
    there are no turns -- the caller should leave those segments under "Others"."""
    if not turns:
        return {}
    assignments = {}
    for seg in segments:
        seg_center = (seg["start_s"] + seg["end_s"]) / 2
        best_speaker = None
        best_overlap = -1.0
        best_distance = float("inf")
        for start, end, speaker in turns:
            overlap = max(0.0, min(seg["end_s"], end) - max(seg["start_s"], start))
            distance = abs((start + end) / 2 - seg_center)
            if overlap > best_overlap or (overlap == best_overlap and distance < best_distance):
                best_speaker = speaker
                best_overlap = overlap
                best_distance = distance
        assignments[seg["id"]] = best_speaker
    return assignments


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def match_voice(
    embedding: list[float], known_voices: list[dict], threshold: float = VOICE_MATCH_THRESHOLD
) -> str | None:
    """The name of whichever known voice this embedding resembles most, by cosine similarity,
    or None if nothing clears `threshold` -- the caller should fall back to "Speaker N" rather
    than guess. `known_voices` is [{"name": str, "embedding": list[float]}, ...], matching
    store.list_voices()'s shape."""
    best_name = None
    best_similarity = threshold
    for voice in known_voices:
        similarity = _cosine_similarity(embedding, voice["embedding"])
        if similarity > best_similarity:
            best_similarity = similarity
            best_name = voice["name"]
    return best_name
