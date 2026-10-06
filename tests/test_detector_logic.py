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
    # The reported app_name is the meeting service (from the window title), not the browser --
    # "Google Meet" is far more useful on a dashboard card than "Google Chrome".
    detector = MeetingDetector()
    titles = [("Google Chrome", "Meet - abc-defg-hij")]
    detector.update(signals(0, {"com.google.Chrome.helper"}, titles))
    assert detector.update(signals(5, {"com.google.Chrome.helper"}, titles)) == Start("Google Meet")


def test_browser_with_zoom_in_the_title_reports_zoom_not_the_browser():
    detector = MeetingDetector()
    titles = [("Google Chrome", "Zoom Meeting")]
    detector.update(signals(0, {"com.google.Chrome.helper"}, titles))
    assert detector.update(signals(5, {"com.google.Chrome.helper"}, titles)) == Start("Zoom")


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


def test_a_browser_tab_switch_does_not_stop_an_active_meeting():
    # Regression: Chrome's OS-reported window title tracks whichever tab is frontmost, so
    # switching away from the Meet tab (or picture-in-picture, or screen-sharing) changes the
    # title without the call ending. This used to be indistinguishable from "meeting over" and
    # split one real meeting into several recordings.
    detector = MeetingDetector()
    meet_titles = [("Google Chrome", "Meet - abc-defg-hij")]
    other_titles = [("Google Chrome", "Inbox - Gmail")]
    detector.update(signals(0, {"com.google.Chrome.helper"}, meet_titles))
    assert detector.update(signals(5, {"com.google.Chrome.helper"}, meet_titles)) == Start(
        "Google Meet"
    )
    # Tabbed away for well past the old 45s stop debounce -- mic usage never drops.
    for t in range(10, 120, 5):
        assert detector.update(signals(t, {"com.google.Chrome.helper"}, other_titles)) is None


def test_the_browser_actually_releasing_the_mic_still_stops_after_the_debounce():
    detector = MeetingDetector()
    meet_titles = [("Google Chrome", "Meet - abc-defg-hij")]
    detector.update(signals(0, {"com.google.Chrome.helper"}, meet_titles))
    detector.update(signals(5, {"com.google.Chrome.helper"}, meet_titles))  # Start
    assert detector.update(signals(10, set())) is None  # mic actually released
    assert detector.update(signals(54, set())) is None  # 44s since the drop
    assert detector.update(signals(55, set())) == Stop()  # 45s since the drop


# --- back-to-back meetings: the mic never releases between them ---


def test_a_genuinely_new_meeting_title_splits_into_a_second_recording():
    # Regression: joining a new Meet call right after the last one, in the same browser tab,
    # never drops the mic -- without checking the title itself, this looked identical to a
    # brief tab-switch and the two calls got silently merged into one recording.
    detector = MeetingDetector()
    first_call = [("Google Chrome", "Meet - abc-defg-hij")]
    second_call = [("Google Chrome", "Meet - xyz-pqrs-tuv")]
    mic = {"com.google.Chrome.helper"}

    detector.update(signals(0, mic, first_call))
    assert detector.update(signals(5, mic, first_call)) == Start("Google Meet")
    assert detector.update(signals(7, mic, first_call)) is None  # same call, no change

    # the new call's title shows up -- mic never drops
    assert detector.update(signals(10, mic, second_call)) is None  # debouncing the change
    assert detector.update(signals(14, mic, second_call)) is None  # 4s since the change, not yet
    assert detector.update(signals(15, mic, second_call)) == Stop()  # 5s since the change

    # the new meeting starts after its own start debounce
    assert detector.update(signals(17, mic, second_call)) is None
    assert detector.update(signals(20, mic, second_call)) == Start("Google Meet")


def test_a_brief_flicker_to_a_different_title_does_not_split_the_meeting():
    # A reconnect banner, an ad, or a loading state can briefly change the title without it
    # being a different call -- only a title that *stays* different counts.
    detector = MeetingDetector()
    first_call = [("Google Chrome", "Meet - abc-defg-hij")]
    flicker = [("Google Chrome", "Meet - reconnecting")]
    mic = {"com.google.Chrome.helper"}

    detector.update(signals(0, mic, first_call))
    assert detector.update(signals(5, mic, first_call)) == Start("Google Meet")

    assert detector.update(signals(10, mic, flicker)) is None  # debouncing the change
    assert detector.update(signals(13, mic, first_call)) is None  # back to normal before 5s
    # no Stop was ever returned, and the meeting is still considered the same one
    for t in range(15, 60, 5):
        assert detector.update(signals(t, mic, first_call)) is None


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
