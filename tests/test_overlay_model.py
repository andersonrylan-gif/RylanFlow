import pytest

from rylanflow.overlay_model import (
    BAR_COUNT,
    FADE_HOLD,
    PULSE_PERIOD,
    OverlayModel,
    scale_level,
)


def test_scale_level_is_zero_for_silence():
    assert scale_level(0.0) == 0.0


def test_scale_level_never_exceeds_one():
    assert scale_level(1.0) == 1.0
    assert scale_level(100.0) == 1.0


def test_scale_level_never_goes_negative_even_for_bad_input():
    assert scale_level(-5.0) == 0.0


def test_scale_level_is_monotonic():
    # Below the saturation point (level * 25 >= 1, i.e. level >= 0.04).
    assert scale_level(0.005) < scale_level(0.01) < scale_level(0.02)


def test_bars_always_has_bar_count_entries():
    model = OverlayModel()
    for level in [0.0, 0.1, 0.3, 0.0]:
        frame = model.update("recording", level, now=0.0)
        assert len(frame.bars) == BAR_COUNT


def test_recording_is_visible_with_bars_and_no_pulse():
    frame = OverlayModel().update("recording", 0.1, now=0.0)
    assert frame.visible is True
    assert frame.mode == "recording"
    assert frame.pulse_phase == 0.0


def test_silence_keeps_bars_near_zero():
    model = OverlayModel()
    frame = None
    for _ in range(5):
        frame = model.update("recording", 0.0, now=0.0)
    assert all(b == 0.0 for b in frame.bars)


def test_loud_level_rises_quickly_attack():
    model = OverlayModel()
    frame = model.update("recording", 0.3, now=0.0)  # one update from silence
    target = scale_level(0.3)
    # Attack is fast: a single update should already be most of the way to the target.
    assert frame.bars[-1] > target * 0.5


def test_level_falls_slowly_after_a_loud_burst_release():
    model = OverlayModel()
    for _ in range(20):  # get the meter up near its ceiling
        model.update("recording", 0.3, now=0.0)
    loud = model.update("recording", 0.3, now=0.0).bars[-1]

    quiet_one_step = model.update("recording", 0.0, now=0.0).bars[-1]

    # Release is slow: one update after silence should still be mostly at the old level.
    assert quiet_one_step > loud * 0.5
    # ...but it should still be trending down.
    assert quiet_one_step < loud


def test_bars_eventually_settle_near_a_sustained_level():
    model = OverlayModel()
    target = scale_level(0.2)
    frame = None
    for _ in range(50):
        frame = model.update("recording", 0.2, now=0.0)
    assert frame.bars[-1] == pytest.approx(target, abs=0.01)


def test_working_mode_has_no_bars_and_is_visible():
    frame = OverlayModel().update("working", 0.0, now=0.0)
    assert frame.visible is True
    assert frame.mode == "working"
    assert frame.bars == []


def test_pulse_phase_cycles_with_time():
    model = OverlayModel()
    assert model.update("working", 0.0, now=0.0).pulse_phase == 0.0
    assert model.update("working", 0.0, now=PULSE_PERIOD / 2).pulse_phase == pytest.approx(0.5)
    assert model.update("working", 0.0, now=PULSE_PERIOD).pulse_phase == pytest.approx(0.0)
    assert model.update("working", 0.0, now=PULSE_PERIOD * 2.25).pulse_phase == pytest.approx(0.25)


def test_idle_stays_visible_during_the_fade_hold_then_hides():
    model = OverlayModel()
    model.update("recording", 0.1, now=0.0)

    just_after = model.update("idle", 0.0, now=0.01)
    assert just_after.visible is True
    assert just_after.mode == "hidden"

    still_fading = model.update("idle", 0.0, now=FADE_HOLD - 0.01)
    assert still_fading.visible is True

    done_fading = model.update("idle", 0.0, now=FADE_HOLD + 0.01)
    assert done_fading.visible is False


def test_returning_to_recording_during_the_fade_cancels_it():
    model = OverlayModel()
    model.update("recording", 0.1, now=0.0)
    model.update("idle", 0.0, now=0.01)  # starts fading

    back = model.update("recording", 0.1, now=0.05)
    assert back.visible is True
    assert back.mode == "recording"

    # The fade timer should have been cleared, not just paused.
    faded_again = model.update("idle", 0.0, now=0.06)
    assert faded_again.visible is True  # fresh fade window, not picking up where it left off


def test_unknown_state_is_treated_like_idle():
    model = OverlayModel()
    frame = model.update("some-future-state", 0.0, now=0.0)
    assert frame.mode == "hidden"
