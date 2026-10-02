from rylanflow.meetings.calendar import _find_meeting_url


def test_finds_a_zoom_url():
    assert _find_meeting_url(["join at https://zoom.us/j/12345"]) == "https://zoom.us/j/12345"


def test_finds_a_google_meet_url():
    url = _find_meeting_url(["https://meet.google.com/abc-defg-hij"])
    assert url == "https://meet.google.com/abc-defg-hij"


def test_finds_a_teams_url():
    assert _find_meeting_url(["https://teams.microsoft.com/l/meetup-join/xyz"]) is not None


def test_finds_a_webex_url():
    assert _find_meeting_url(["https://company.webex.com/meet/room"]) is not None


def test_prefers_the_first_matching_text():
    url = _find_meeting_url([None, "https://zoom.us/j/1", "https://meet.google.com/x"])
    assert url == "https://zoom.us/j/1"


def test_skips_none_entries():
    assert _find_meeting_url([None, None, "https://zoom.us/j/1"]) == "https://zoom.us/j/1"


def test_no_match_returns_none():
    assert _find_meeting_url(["just a regular note", None]) is None


def test_empty_list_returns_none():
    assert _find_meeting_url([]) is None


def test_is_case_insensitive():
    assert _find_meeting_url(["HTTPS://ZOOM.US/J/12345"]) is not None
