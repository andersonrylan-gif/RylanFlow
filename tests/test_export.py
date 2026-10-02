from rylanflow.meetings.export import to_markdown


def meeting(**overrides):
    base = {
        "title": "Standup",
        "started_at": "2026-10-02T12:00:00+00:00",
        "source_app": "Zoom",
        "attendees": [],
        "speakers": [{"id": 1, "label": "You", "display_name": "Rylan", "is_me": 1}],
        "segments": [],
    }
    base.update(overrides)
    return base


def test_title_and_date_header():
    md = to_markdown(meeting())
    assert md.startswith("# Standup\n")
    assert "**Date:** 2026-10-02T12:00:00+00:00" in md


def test_falls_back_to_source_app_when_no_title():
    md = to_markdown(meeting(title=None))
    assert md.startswith("# Meeting with Zoom\n")


def test_falls_back_to_unknown_app_when_neither_title_nor_source_app():
    md = to_markdown(meeting(title=None, source_app=None))
    assert md.startswith("# Meeting with unknown app\n")


def test_attendees_line_only_when_present():
    assert "**Attendees:**" not in to_markdown(meeting())
    md = to_markdown(meeting(attendees=["Sarah", "John"]))
    assert "**Attendees:** Sarah, John" in md


def test_segments_use_display_name_and_mmss_timestamp():
    md = to_markdown(
        meeting(
            segments=[
                {"speaker_id": 1, "start_s": 0.0, "end_s": 1.0, "text": "hello"},
                {"speaker_id": 1, "start_s": 75.0, "end_s": 76.0, "text": "world"},
            ]
        )
    )
    assert "**Rylan** [0:00]: hello" in md
    assert "**Rylan** [1:15]: world" in md


def test_segment_falls_back_to_speaker_label_with_no_display_name():
    md = to_markdown(
        meeting(
            speakers=[{"id": 2, "label": "Others", "display_name": None, "is_me": 0}],
            segments=[{"speaker_id": 2, "start_s": 0.0, "end_s": 1.0, "text": "hi"}],
        )
    )
    assert "**Others** [0:00]: hi" in md


def test_segment_with_unknown_speaker_id():
    md = to_markdown(
        meeting(segments=[{"speaker_id": 999, "start_s": 0.0, "end_s": 1.0, "text": "hi"}])
    )
    assert "**Unknown** [0:00]: hi" in md
