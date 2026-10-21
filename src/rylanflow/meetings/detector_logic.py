"""Pure decision logic for auto-record: turns detector.Signals into a Start/Stop/None decision.
No AppKit or CoreAudio here, so it's fully unit-testable with fabricated Signals -- detector.py
is the (untested, impure) module that actually probes the OS.

Debounced in both directions: a meeting-looking signal must hold for START_DEBOUNCE_S before we
start recording (so a one-off mic blip doesn't trigger it), and must be absent for
STOP_DEBOUNCE_S before we stop (so a mute toggle or a momentary tab switch doesn't end it).
"""

import dataclasses
import re

START_DEBOUNCE_S = 5.0
STOP_DEBOUNCE_S = 45.0
# How long a different Google Meet call code must be the only one on screen, while a meeting's
# already active, before treating it as a genuinely new call rather than a passing glitch.
TITLE_CHANGE_DEBOUNCE_S = 15.0
# A Google Meet call code, as it appears in the tab title ("Meet - abc-defg-hij").
MEET_CODE = re.compile(r"\b[a-z]{3}-[a-z]{4}-[a-z]{3}\b")

# Bundle IDs of apps whose mic use alone means "probably in a meeting" (see docs/decisions/
# 0003-system-audio.md's appendix for how these were chosen / verified).
MEETING_APP_LABELS = {
    "us.zoom.xos": "Zoom",
    "com.microsoft.teams2": "Microsoft Teams",
    "com.apple.FaceTime": "FaceTime",
    "com.cisco.webexmeetingsapp": "Webex",
}
SLACK_BUNDLE_ID = "com.tinyspeck.slackmacgap"
HUDDLE_TITLE_MARKER = "Huddle"

# Browsers only count as "in a meeting" alongside a meeting-looking window title -- being a
# browser using the mic isn't enough on its own (could be any site with a mic prompt).
BROWSER_BUNDLE_PREFIXES = (
    "com.google.Chrome",
    "com.apple.Safari",
    "com.apple.WebKit",
    "company.thebrowser.Browser",
    "com.microsoft.edgemac",
    "org.mozilla.firefox",
    "com.brave.Browser",
)
BROWSER_OWNER_NAMES = {
    "Google Chrome",
    "Safari",
    "WebKit",
    "Arc",
    "Microsoft Edge",
    "Firefox",
    "Brave Browser",
}
# Maps a window-title substring to the friendly service name reported as the meeting's
# source_app -- "Google Meet" is far more useful on a dashboard card than "Google Chrome" (the
# browser is almost never the thing worth naming the meeting after).
MEETING_TITLE_LABELS = {
    "Meet -": "Google Meet",
    "Google Meet": "Google Meet",
    "Zoom": "Zoom",
    "Microsoft Teams": "Microsoft Teams",
    "Webex": "Webex",
}


@dataclasses.dataclass
class Start:
    app_name: str


@dataclasses.dataclass
class Stop:
    pass


def _looks_like_a_meeting(mic_bundle_ids: set, window_titles: list) -> tuple[bool, str | None]:
    for bundle_id in mic_bundle_ids:
        if label := MEETING_APP_LABELS.get(bundle_id):
            return True, label

    if SLACK_BUNDLE_ID in mic_bundle_ids:
        if any(HUDDLE_TITLE_MARKER in title for _owner, title in window_titles):
            return True, "Slack"

    if any(
        bundle_id.startswith(prefix)
        for bundle_id in mic_bundle_ids
        for prefix in BROWSER_BUNDLE_PREFIXES
    ):
        for owner, title in window_titles:
            if owner not in BROWSER_OWNER_NAMES:
                continue
            for marker, label in MEETING_TITLE_LABELS.items():
                if marker in title:
                    return True, label

    return False, None


def _meet_codes(window_titles: list) -> frozenset[str]:
    """Every Google Meet call code in a browser window title. The code is the only reliable way
    to tell one call from the next: the rest of the title changes for many reasons mid-call
    (presenting, picture-in-picture, a Calendar or Meet home tab that also says "Google Meet"),
    and splitting on those cut real meetings into many short recordings, each with its own
    notification. Several codes can be present at once (two Meet tabs)."""
    return frozenset(
        code
        for owner, title in window_titles
        if owner in BROWSER_OWNER_NAMES and "Meet" in title
        for code in MEET_CODE.findall(title)
    )


def _mic_plausibly_in_a_meeting(mic_bundle_ids: set) -> bool:
    """Looser than _looks_like_a_meeting's title check -- used only to SUSTAIN an already-
    active meeting. Requiring the window title to keep matching caused real meetings to be cut
    into pieces: switching tabs, picture-in-picture, and screen-sharing all change what title
    macOS reports for the window, without the call actually ending. Starting a new recording
    still requires the stricter title-matched signal in _looks_like_a_meeting."""
    if mic_bundle_ids & MEETING_APP_LABELS.keys():
        return True
    if SLACK_BUNDLE_ID in mic_bundle_ids:
        return True
    return any(
        bundle_id.startswith(prefix)
        for bundle_id in mic_bundle_ids
        for prefix in BROWSER_BUNDLE_PREFIXES
    )


class MeetingDetector:
    """Call update() on every poll (e.g. every 2s) with a fresh detector.Signals. Keeps its own
    debounce timers, so each call only needs the latest reading."""

    def __init__(
        self,
        start_debounce: float = START_DEBOUNCE_S,
        stop_debounce: float = STOP_DEBOUNCE_S,
        title_change_debounce: float = TITLE_CHANGE_DEBOUNCE_S,
    ) -> None:
        self._start_debounce = start_debounce
        self._stop_debounce = stop_debounce
        self._title_change_debounce = title_change_debounce
        self._meeting_since: float | None = None
        self._not_meeting_since: float | None = None
        self._active = False
        self._manually_stopped = False
        # The Meet call codes the active recording belongs to, and when a different call's code
        # first became the only one on screen -- for noticing a new call that starts without the
        # mic ever releasing (back-to-back calls in the same browser tab). See _meet_codes.
        self._active_codes: frozenset[str] = frozenset()
        self._different_codes_since: float | None = None

    def note_external_start(self) -> None:
        """Call after a meeting starts through some path other than this detector's own Start
        decision (the menu, or the dashboard), so the bookkeeping doesn't drift and try to
        auto-start a second one."""
        self._active = True
        self._manually_stopped = False
        self._meeting_since = None
        self._active_codes = frozenset()
        self._different_codes_since = None

    def note_external_stop(self) -> None:
        """Call after a meeting is stopped through some path other than this detector's own Stop
        decision, so it doesn't immediately try to auto-start it again while the same meeting is
        still ongoing -- the override lasts until the meeting-looking signal goes false and
        comes back."""
        self._active = False
        self._manually_stopped = True
        self._not_meeting_since = None
        self._active_codes = frozenset()
        self._different_codes_since = None

    def update(self, signals) -> Start | Stop | None:
        meeting_like, app_name = _looks_like_a_meeting(
            signals.mic_bundle_ids, signals.window_titles
        )
        codes = _meet_codes(signals.window_titles) if meeting_like else frozenset()
        now = signals.now

        if self._active and (decision := self._check_for_new_call(codes, now)):
            return decision

        # Once active, a plain mic-usage check is enough to keep the meeting going -- see
        # _mic_plausibly_in_a_meeting's docstring for why the stricter title check is only
        # appropriate for deciding whether to *start* a new recording.
        sustaining = self._active and _mic_plausibly_in_a_meeting(signals.mic_bundle_ids)

        if not meeting_like and not sustaining:
            self._meeting_since = None
            self._manually_stopped = False  # the meeting ended; a future one can auto-start
            if self._active:
                if self._not_meeting_since is None:
                    self._not_meeting_since = now
                elif now - self._not_meeting_since >= self._stop_debounce:
                    self._active = False
                    self._not_meeting_since = None
                    self._active_codes = frozenset()
                    return Stop()
            return None

        self._not_meeting_since = None
        if not meeting_like:
            return None  # sustaining on mic usage alone -- the meeting's already active
        if self._meeting_since is None:
            self._meeting_since = now
        if self._active or self._manually_stopped:
            return None
        if now - self._meeting_since >= self._start_debounce:
            self._active = True
            self._meeting_since = None
            self._active_codes = codes
            self._different_codes_since = None
            return Start(app_name)
        return None

    def _check_for_new_call(self, codes: frozenset[str], now: float) -> Stop | None:
        """Stop the active recording only once a *different* Meet call code has been the only
        one on screen for TITLE_CHANGE_DEBOUNCE_S -- the active call's own code must be gone
        from every window. Anything else (no code visible, a second Meet tab alongside the
        call, a title that changed for some other reason) keeps the same recording going."""
        if not self._active_codes:
            self._active_codes = codes  # first codes seen for this meeting, if any
            return None
        if not codes or codes & self._active_codes:
            self._different_codes_since = None
            return None
        if self._different_codes_since is None:
            self._different_codes_since = now
            return None
        if now - self._different_codes_since < self._title_change_debounce:
            return None
        self._active = False
        self._active_codes = frozenset()
        self._not_meeting_since = None
        self._different_codes_since = None
        self._meeting_since = now  # the new call's own start-debounce begins now
        return Stop()
