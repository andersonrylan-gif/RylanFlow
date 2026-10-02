import asyncio

import pytest

from rylanflow.mcp_server import build_server
from rylanflow.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


@pytest.fixture
def server(store):
    return build_server(store)


def call(server, name: str, arguments: dict | None = None):
    """Call a tool the way a real MCP client would, and return its structured result."""
    result = asyncio.run(server.call_tool(name, arguments or {}))
    assert not result.is_error, result.content
    return result.structured_content["result"]


def test_list_dictations_returns_recent_dictations(server, store):
    store.add_dictation("hello world", 1.0, "Notes", "base")
    rows = call(server, "list_dictations")
    assert len(rows) == 1
    assert rows[0]["text"] == "hello world"


def test_list_dictations_filters_by_query(server, store):
    store.add_dictation("the quick fox", 1.0, None, None)
    store.add_dictation("a lazy dog", 1.0, None, None)
    rows = call(server, "list_dictations", {"query": "fox"})
    assert len(rows) == 1
    assert "fox" in rows[0]["text"]


def test_list_dictations_respects_limit(server, store):
    for i in range(5):
        store.add_dictation(f"item {i}", 1.0, None, None)
    rows = call(server, "list_dictations", {"limit": 2})
    assert len(rows) == 2


def test_list_meetings_returns_recent_meetings(server, store):
    store.create_meeting(title="Standup")
    rows = call(server, "list_meetings")
    assert len(rows) == 1
    assert rows[0]["title"] == "Standup"


def test_list_meetings_filters_by_query(server, store):
    store.create_meeting(title="Standup")
    store.create_meeting(title="Planning")
    rows = call(server, "list_meetings", {"query": "Planning"})
    assert len(rows) == 1
    assert rows[0]["title"] == "Planning"


def test_list_meetings_respects_limit(server, store):
    for i in range(5):
        store.create_meeting(title=f"meeting {i}")
    rows = call(server, "list_meetings", {"limit": 2})
    assert len(rows) == 2


def test_get_meeting_transcript_includes_speakers_and_segments(server, store):
    mid = store.create_meeting(title="Standup")
    you = store.add_speaker(mid, "You", is_me=True)
    store.add_segments(
        mid, [{"speaker_id": you, "track": "mic", "start_s": 0, "end_s": 1, "text": "hello"}]
    )
    meeting = call(server, "get_meeting_transcript", {"meeting_id": mid})
    assert meeting["title"] == "Standup"
    assert len(meeting["segments"]) == 1
    assert meeting["segments"][0]["text"] == "hello"


def test_get_meeting_transcript_for_unknown_id_returns_none(server):
    assert call(server, "get_meeting_transcript", {"meeting_id": 999}) is None
