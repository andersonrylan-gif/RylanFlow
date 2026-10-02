"""Pure state machine for the dictation pop-up: turns (state, mic level, time) into what to
draw. No AppKit here, so it's fully unit-testable; overlay.py (AppKit) just renders whatever
Frame this produces, on a timer, without having to know anything about audio or timing.
"""

from dataclasses import dataclass, field

BAR_COUNT = 9
ATTACK = 0.6  # per-update blend toward a louder target: quick, so peaks register immediately
RELEASE = 0.15  # per-update blend toward a quieter target: slow, so the meter doesn't flicker
FADE_HOLD = 0.25  # seconds the panel stays visible after going idle, so it fades, not blinks
PULSE_PERIOD = 1.2  # seconds for one "working" dot-pulse cycle


def scale_level(level: float) -> float:
    """Maps a raw RMS mic level to a 0..1 bar height. Normal speech is quiet in RMS terms
    (saturates to 1.0 once level >= 0.04), and the exponent makes softer sounds still visibly
    move the meter, instead of everything short of shouting looking flat."""
    return min(1.0, (max(0.0, level) * 25) ** 0.6)


@dataclass
class Frame:
    visible: bool
    mode: str  # "recording" | "working" | "hidden"
    bars: list[float] = field(default_factory=list)  # BAR_COUNT heights, only for "recording"
    pulse_phase: float = 0.0  # 0..1, only for "working"


class OverlayModel:
    """Call update() on every render tick (e.g. a 30fps timer) with the current pipeline state
    and mic level. Keeps its own history, so each call only needs the latest reading."""

    def __init__(self) -> None:
        self._bars = [0.0] * BAR_COUNT
        self._hidden_since: float | None = None  # when the state last went idle, or None

    def update(self, state: str, level: float, now: float) -> Frame:
        if state == "recording":
            self._hidden_since = None
            self._bars = self._bars[1:] + [self._smoothed_bar(level)]
            return Frame(visible=True, mode="recording", bars=list(self._bars))

        if state == "working":
            self._hidden_since = None
            return Frame(
                visible=True, mode="working", pulse_phase=(now % PULSE_PERIOD) / PULSE_PERIOD
            )

        # Anything else (idle, etc.): fade out instead of disappearing the instant we're done.
        if self._hidden_since is None:
            self._hidden_since = now
        still_fading = now - self._hidden_since < FADE_HOLD
        return Frame(visible=still_fading, mode="hidden")

    def _smoothed_bar(self, level: float) -> float:
        target = scale_level(level)
        current = self._bars[-1] if self._bars else 0.0
        rate = ATTACK if target > current else RELEASE
        return current + (target - current) * rate
