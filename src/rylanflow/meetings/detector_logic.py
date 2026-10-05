"""Pure decision logic for auto-record: turns detector.Signals into a Start/Stop/None decision.
No AppKit or CoreAudio here, so it's fully unit-testable with fabricated Signals -- detector.py
is the (untested, impure) module that actually probes the OS.

Debounced in both directions: a meeting-looking signal must hold for START_DEBOUNCE_S before we
start recording (so a one-off mic blip doesn't trigger it), and must be absent for
STOP_DEBOUNCE_S before we stop (so a mute toggle or a momentary tab switch doesn't end it).
"""

import dataclasses

START_DEBOUNCE_S = 5.0
STOP_DEBOUNCE_S = 45.0

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


class MeetingDetector:
    """Call update() on every poll (e.g. every 2s) with a fresh detector.Signals. Keeps its own
    debounce timers, so each call only needs the latest reading."""

    def __init__(
        self, start_debounce: float = START_DEBOUNCE_S, stop_debounce: float = STOP_DEBOUNCE_S
    ) -> None:
        self._start_debounce = start_debounce
        self._stop_debounce = stop_debounce
        self._meeting_since: float | None = None
        self._not_meeting_since: float | None = None
        self._active = False
        self._manually_stopped = False

    def note_external_start(self) -> None:
        """Call after a meeting starts through some path other than this detector's own Start
        decision (the menu, or the dashboard), so the bookkeeping doesn't drift and try to
        auto-start a second one."""
        self._active = True
        self._manually_stopped = False
        self._meeting_since = None

    def note_external_stop(self) -> None:
        """Call after a meeting is stopped through some path other than this detector's own Stop
        decision, so it doesn't immediately try to auto-start it again while the same meeting is
        still ongoing -- the override lasts until the meeting-looking signal goes false and
        comes back."""
        self._active = False
        self._manually_stopped = True
        self._not_meeting_since = None

    def update(self, signals) -> Start | Stop | None:
        meeting_like, app_name = _looks_like_a_meeting(
            signals.mic_bundle_ids, signals.window_titles
        )
        now = signals.now

        if not meeting_like:
            self._meeting_since = None
            self._manually_stopped = False  # the meeting ended; a future one can auto-start
            if self._active:
                if self._not_meeting_since is None:
                    self._not_meeting_since = now
                elif now - self._not_meeting_since >= self._stop_debounce:
                    self._active = False
                    self._not_meeting_since = None
                    return Stop()
            return None

        self._not_meeting_since = None
        if self._meeting_since is None:
            self._meeting_since = now
        if self._active or self._manually_stopped:
            return None
        if now - self._meeting_since >= self._start_debounce:
            self._active = True
            self._meeting_since = None
            return Start(app_name)
        return None
