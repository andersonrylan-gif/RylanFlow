import numpy as np
import pytest

from rylanflow.meetings.chunker import Chunker

SAMPLE_RATE = 16_000


def tone(seconds: float, freq: float = 440.0, amplitude: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def seconds(audio: np.ndarray) -> float:
    return audio.size / SAMPLE_RATE


def test_push_returns_nothing_until_max_s_is_reached():
    chunker = Chunker(sample_rate=SAMPLE_RATE)
    windows = chunker.push(tone(10))
    assert windows == []


def test_cut_point_falls_within_a_known_silence_gap():
    # Tone, then a silence gap spanning most of the search window [target_s, max_s), then tone.
    chunker = Chunker(sample_rate=SAMPLE_RATE, target_s=30, max_s=40, min_silence_s=0.4)
    audio = np.concatenate([tone(28), silence(5), tone(12)])  # silence covers [28, 33)

    windows = chunker.push(audio)

    assert len(windows) == 1
    offset, chunk = windows[0]
    assert offset == 0
    cut_at = seconds(chunk)
    assert 28.0 <= cut_at <= 33.0, f"cut at {cut_at}s should fall inside the silence gap"


def test_cut_prefers_the_quietest_point_in_the_search_window():
    # Two silence gaps in the search window; a shorter, quieter one should win over a longer,
    # noisier (but not actually silent) one.
    chunker = Chunker(sample_rate=SAMPLE_RATE, target_s=30, max_s=40, min_silence_s=0.4)
    quiet_gap_start = 30.0
    loud_gap = tone(3, amplitude=0.05)  # quiet-ish, but not silent
    audio = np.concatenate(
        [tone(quiet_gap_start), silence(1.0), tone(2), loud_gap, tone(4)]
    )  # true silence at [30, 31), a quieter-but-not-silent patch later

    windows = chunker.push(audio)

    offset, chunk = windows[0]
    cut_at = seconds(chunk)
    assert 30.0 <= cut_at < 31.0, f"cut at {cut_at}s should land in the truly silent gap"


def test_offsets_accumulate_across_multiple_chunks():
    chunker = Chunker(sample_rate=SAMPLE_RATE, target_s=30, max_s=40, min_silence_s=0.4)
    # Enough audio, with a clear silence gap every ~35s, to produce two cuts in one push().
    one_round = np.concatenate([tone(33), silence(2)])
    audio = np.concatenate([one_round, one_round, tone(5)])

    windows = chunker.push(audio)

    assert len(windows) == 2
    first_offset, first_chunk = windows[0]
    second_offset, second_chunk = windows[1]
    assert first_offset == 0
    assert second_offset == pytest.approx(seconds(first_chunk), abs=1e-6)


def test_flush_returns_the_remainder_and_then_nothing():
    chunker = Chunker(sample_rate=SAMPLE_RATE, target_s=30, max_s=40)
    chunker.push(tone(10))  # well under max_s, so push() hands back nothing yet

    windows = chunker.flush()

    assert len(windows) == 1
    offset, chunk = windows[0]
    assert offset == 0
    assert seconds(chunk) == pytest.approx(10.0)
    assert chunker.flush() == []  # nothing left the second time


def test_flush_after_a_cut_uses_the_advanced_offset():
    chunker = Chunker(sample_rate=SAMPLE_RATE, target_s=30, max_s=40, min_silence_s=0.4)
    # 43s total (> max_s=40), so this triggers one cut, leaving a few seconds buffered.
    cut_windows = chunker.push(np.concatenate([tone(28), silence(5), tone(10)]))
    assert len(cut_windows) == 1
    _, first_chunk = cut_windows[0]

    [(offset, chunk)] = chunker.flush()

    assert offset == pytest.approx(seconds(first_chunk), abs=1e-6)
    assert seconds(chunk) > 0


def test_hard_cut_when_nothing_is_quiet():
    # Constant tone for well over max_s: there's no silence anywhere, so the chunker must still
    # cut -- it can't buffer forever -- and must never exceed max_s.
    chunker = Chunker(sample_rate=SAMPLE_RATE, target_s=30, max_s=40, min_silence_s=0.4)

    windows = chunker.push(tone(45, amplitude=0.5))

    assert len(windows) == 1
    offset, chunk = windows[0]
    assert seconds(chunk) == pytest.approx(40.0, abs=0.05)


def test_falls_back_to_a_hard_cut_when_the_search_window_is_too_narrow():
    # max_s - target_s is smaller than min_silence_s: there's no room to search for a quiet
    # point at all, so this must not crash, and must still cut at (or before) max_s.
    chunker = Chunker(sample_rate=SAMPLE_RATE, target_s=39.8, max_s=40.0, min_silence_s=0.5)

    windows = chunker.push(tone(42))

    assert len(windows) == 1
    offset, chunk = windows[0]
    assert seconds(chunk) <= 40.0 + 1e-6


def test_push_with_empty_audio_is_a_noop():
    chunker = Chunker(sample_rate=SAMPLE_RATE)
    assert chunker.push(np.zeros(0, dtype=np.float32)) == []
    assert chunker.flush() == []
