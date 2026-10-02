import numpy as np

from rylanflow.meetings.speakers import is_echo, is_silent

SAMPLE_RATE = 16_000


def tone(seconds: float, amplitude: float = 0.3) -> np.ndarray:
    return np.full(int(seconds * SAMPLE_RATE), amplitude, dtype=np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


# --- is_silent ---


def test_is_silent_true_for_zero_audio():
    assert is_silent(silence(1.0)) is True


def test_is_silent_false_for_loud_audio():
    assert is_silent(tone(1.0, amplitude=0.3)) is False


def test_is_silent_true_for_empty_audio():
    assert is_silent(np.zeros(0, dtype=np.float32)) is True


def test_is_silent_respects_custom_threshold():
    quiet = tone(1.0, amplitude=0.02)
    assert is_silent(quiet, threshold=0.01) is False
    assert is_silent(quiet, threshold=0.05) is True


# --- is_echo ---


def test_is_echo_true_when_overlapping_and_similar():
    system_segments = [(10.0, 12.0, "hello there how are you")]
    assert is_echo("hello there how are you", 10.1, 11.9, system_segments) is True


def test_is_echo_false_when_no_overlap():
    system_segments = [(10.0, 12.0, "hello there how are you")]
    assert is_echo("hello there how are you", 20.0, 22.0, system_segments) is False


def test_is_echo_false_when_overlapping_but_different_words():
    system_segments = [(10.0, 12.0, "hello there how are you")]
    assert is_echo("completely unrelated sentence", 10.1, 11.9, system_segments) is False


def test_is_echo_false_when_overlap_is_too_small():
    # Mic segment [10, 20); system segment only overlaps [10, 10.5) -- 5% of the mic segment.
    system_segments = [(9.0, 10.5, "hello there how are you")]
    assert is_echo("hello there how are you", 10.0, 20.0, system_segments) is False


def test_is_echo_true_at_exactly_the_overlap_threshold():
    # Mic segment [10, 20) (10s); system segment [10, 15) overlaps exactly 50% of it.
    system_segments = [(10.0, 15.0, "identical words here")]
    assert is_echo("identical words here", 10.0, 20.0, system_segments) is True


def test_is_echo_checks_multiple_system_segments():
    system_segments = [
        (0.0, 1.0, "unrelated"),
        (10.0, 12.0, "hello there how are you"),
    ]
    assert is_echo("hello there how are you", 10.1, 11.9, system_segments) is True


def test_is_echo_false_with_no_system_segments():
    assert is_echo("anything", 0.0, 5.0, []) is False


def test_is_echo_is_case_insensitive():
    system_segments = [(10.0, 12.0, "HELLO THERE")]
    assert is_echo("hello there", 10.0, 12.0, system_segments) is True
