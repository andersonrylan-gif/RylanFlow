import numpy as np

from rylanflow.meetings.speakers import assign, is_echo, is_silent, merge_short_speakers

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


# --- assign ---


def seg(id_, start, end):
    return {"id": id_, "start_s": start, "end_s": end}


def test_assign_with_no_turns_returns_nothing():
    assert assign([seg(1, 0.0, 5.0)], []) == {}


def test_assign_single_speaker():
    turns = [(0.0, 10.0, 0)]
    segments = [seg(1, 0.0, 3.0), seg(2, 3.0, 6.0), seg(3, 6.0, 10.0)]
    assert assign(segments, turns) == {1: 0, 2: 0, 3: 0}


def test_assign_two_alternating_speakers():
    turns = [(0.0, 5.0, 0), (5.0, 10.0, 1), (10.0, 15.0, 0)]
    segments = [seg(1, 0.0, 4.0), seg(2, 6.0, 9.0), seg(3, 11.0, 14.0)]
    assert assign(segments, turns) == {1: 0, 2: 1, 3: 0}


def test_assign_segment_spanning_two_turns_picks_the_larger_overlap():
    turns = [(0.0, 3.0, 0), (3.0, 20.0, 1)]
    # segment [1, 10): 2s with speaker 0, 7s with speaker 1 -- speaker 1 wins
    segments = [seg(1, 1.0, 10.0)]
    assert assign(segments, turns) == {1: 1}


def test_assign_falls_back_to_the_nearest_turn_when_nothing_overlaps():
    turns = [(0.0, 2.0, 0), (100.0, 102.0, 1)]
    # segment [10, 12) overlaps neither turn; turn 0's center (1.0) is closer than turn 1's (101.0)
    segments = [seg(1, 10.0, 12.0)]
    assert assign(segments, turns) == {1: 0}


# --- merge_short_speakers ---


def test_merge_short_speakers_with_no_turns():
    assert merge_short_speakers([], min_total_duration=5.0) == []


def test_merge_short_speakers_leaves_real_speakers_alone():
    turns = [(0.0, 10.0, 0), (10.0, 20.0, 1)]
    assert merge_short_speakers(turns, min_total_duration=5.0) == turns


def test_merge_short_speakers_folds_a_brief_speaker_into_the_nearest_real_one():
    # speaker 2 only ever talks for 1s total (two 0.5s blips) -- noise, gets merged away.
    turns = [
        (0.0, 10.0, 0),  # speaker 0: 10s, real
        (10.0, 10.5, 2),  # speaker 2: 0.5s, noise, nearest to speaker 0's turn
        (20.0, 30.0, 1),  # speaker 1: 10s, real
        (30.5, 31.0, 2),  # speaker 2: another 0.5s blip, nearest to speaker 1's turn
    ]
    merged = merge_short_speakers(turns, min_total_duration=5.0)
    assert merged == [
        (0.0, 10.0, 0),
        (10.0, 10.5, 0),  # merged into speaker 0 (nearer than speaker 1)
        (20.0, 30.0, 1),
        (30.5, 31.0, 1),  # merged into speaker 1 (nearer than speaker 0)
    ]


def test_merge_short_speakers_never_changes_timing_only_speaker_ids():
    turns = [(0.0, 10.0, 0), (10.0, 10.5, 2)]
    merged = merge_short_speakers(turns, min_total_duration=5.0)
    assert [(s, e) for s, e, _ in merged] == [(s, e) for s, e, _ in turns]


def test_merge_short_speakers_with_everything_short_changes_nothing():
    # No speaker clears the bar -- nothing to merge into, so leave it all as-is.
    turns = [(0.0, 1.0, 0), (1.0, 2.0, 1)]
    assert merge_short_speakers(turns, min_total_duration=5.0) == turns


def test_merge_short_speakers_exactly_at_the_threshold_counts_as_real():
    turns = [(0.0, 5.0, 0), (5.0, 15.0, 1)]
    assert merge_short_speakers(turns, min_total_duration=5.0) == turns
