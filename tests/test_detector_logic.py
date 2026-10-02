from rylanflow.meetings.detector import Signals
from rylanflow.meetings.detector_logic import MeetingDetector, Start, Stop


def signals(now, mic_bundle_ids=frozenset(), window_titles=()):
    return Signals(set(mic_bundle_ids), list(window_titles), now)


def test_no_signal_never_starts():
    detector = MeetingDetector()
    for t in range(0, 60, 2):
        assert detector.update(signals(t)) is None


def test_meeting_app_starts_after_the_debounce():
    detector = MeetingDetector()
    assert detector.update(signals(0, {"us.zoom.xos"})) is None
    assert detector.update(signals(3, {"us.zoom.xos"})) is None
    assert detector.update(signals(5, {"us.zoom.xos"})) == Start("Zoom")


def test_a_brief_blip_never_starts():
    detector = MeetingDetector()
    assert detector.update(signals(0, {"us.zoom.xos"})) is None
    assert detector.update(signals(2, {"us.zoom.xos"})) is None
    assert detector.update(signals(4, set())) is None  # the blip ends before 5s
    assert detector.update(signals(9, set())) is None  # still nothing, timer reset
    assert detector.update(signals(20, set())) is None


def test_once_active_it_does_not_start_again():
    detector = MeetingDetector()
    detector.update(signals(0, {"us.zoom.xos"}))
    assert detector.update(signals(5, {"us.zoom.xos"})) == Start("Zoom")
    assert detector.update(signals(6, {"us.zoom.xos"})) is None
    assert detector.update(signals(100, {"us.zoom.xos"})) is None


def test_stops_after_45s_of_no_signal():
    detector = MeetingDetector()
    detector.update(signals(0, {"us.zoom.xos"}))
    detector.update(signals(5, {"us.zoom.xos"}))  # Start
    assert detector.update(signals(10, set())) is None
    assert detector.update(signals(50, set())) is None  # 40s of silence, not yet 45
    assert detector.update(signals(55, set())) == Stop()


def test_a_mute_toggle_within_the_stop_debounce_does_not_stop():
    detector = MeetingDetector()
    detector.update(signals(0, {"us.zoom.xos"}))
    detector.update(signals(5, {"us.zoom.xos"}))  # Start
    assert detector.update(signals(10, set())) is None  # mic drops (muted)
    assert detector.update(signals(30, {"us.zoom.xos"})) is None  # unmuted again, still active
    # the not-meeting timer only (re)starts once the signal actually drops again, not from the
    # earlier blip at t=10 -- so it takes another 45s from this next drop to actually stop
    assert detector.update(signals(60, set())) is None  # the drop; timer starts here
    assert detector.update(signals(100, set())) is None  # only 40s since the t=60 drop
    assert detector.update(signals(106, set())) == Stop()  # 46s since the t=60 drop


def test_browser_with_a_meeting_title_starts_it():
    detector = MeetingDetector()
    titles = [("Google Chrome", "Meet - abc-defg-hij")]
    detector.update(signals(0, {"com.google.Chrome.helper"}, titles))
    assert detector.update(signals(5, {"com.google.Chrome.helper"}, titles)) == Start(
        "Google Chrome"
    )


def test_browser_without_a_meeting_title_never_starts():
    detector = MeetingDetector()
    titles = [("Google Chrome", "Inbox - Gmail")]
    for t in range(0, 60, 2):
        assert detector.update(signals(t, {"com.google.Chrome.helper"}, titles)) is None


def test_krisp_alone_never_starts():
    # Regression: Krisp (noise cancellation) shows running_input=1 alongside a real meeting app,
    # but on its own it isn't a meeting signal (see docs/decisions/0003-system-audio.md appendix).
    detector = MeetingDetector()
    for t in range(0, 60, 2):
        assert detector.update(signals(t, {"ai.krisp.krispMac"})) is None


def test_slack_without_a_huddle_title_never_starts():
    detector = MeetingDetector()
    for t in range(0, 60, 2):
        assert detector.update(signals(t, {"com.tinyspeck.slackmacgap"})) is None


def test_slack_with_a_huddle_title_starts():
    detector = MeetingDetector()
    titles = [("Slack", "general — Huddle")]
    detector.update(signals(0, {"com.tinyspeck.slackmacgap"}, titles))
    assert detector.update(signals(5, {"com.tinyspeck.slackmacgap"}, titles)) == Start("Slack")


def test_note_external_start_prevents_a_duplicate_start():
    detector = MeetingDetector()
    detector.note_external_start()
    for t in range(0, 60, 2):
        assert detector.update(signals(t, {"us.zoom.xos"})) is None


def test_note_external_start_still_auto_stops_once_the_signal_drops():
    detector = MeetingDetector()
    detector.note_external_start()
    assert detector.update(signals(0, {"us.zoom.xos"})) is None
    assert detector.update(signals(10, set())) is None  # the drop; timer starts here
    assert detector.update(signals(54, set())) is None  # 44s since the drop
    assert detector.update(signals(55, set())) == Stop()  # 45s since the drop


def test_manual_stop_overrides_auto_start_until_the_signal_goes_false():
    detector = MeetingDetector()
    detector.update(signals(0, {"us.zoom.xos"}))
    assert detector.update(signals(5, {"us.zoom.xos"})) == Start("Zoom")

    detector.note_external_stop()
    # the meeting is still visibly happening, but the override should hold
    for t in range(6, 60, 2):
        assert detector.update(signals(t, {"us.zoom.xos"})) is None

    # once the signal actually drops (call ends) and a new one starts, auto-record resumes
    assert detector.update(signals(60, set())) is None
    assert detector.update(signals(65, {"us.zoom.xos"})) is None
    assert detector.update(signals(70, {"us.zoom.xos"})) == Start("Zoom")
