from datetime import UTC, datetime, timedelta

import pytest

from rylanflow.store import Store


class Clock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock():
    return Clock(datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def store(tmp_path, clock):
    s = Store(tmp_path / "test.db", clock=clock)
    yield s
    s.close()


# --- dictations ---


def test_add_and_list_dictation(store):
    id_ = store.add_dictation("hello world", 2.5, "Notes", "base")
    rows = store.list_dictations()
    assert len(rows) == 1
    assert rows[0]["id"] == id_
    assert rows[0]["text"] == "hello world"
    assert rows[0]["seconds"] == 2.5
    assert rows[0]["app_name"] == "Notes"
    assert rows[0]["model"] == "base"
    assert rows[0]["created_at"]


def test_list_dictations_newest_first(store):
    store.add_dictation("first", 1, None, None)
    store.add_dictation("second", 1, None, None)
    rows = store.list_dictations()
    assert [r["text"] for r in rows] == ["second", "first"]


def test_list_dictations_pagination(store):
    for i in range(5):
        store.add_dictation(f"item {i}", 1, None, None)
    page1 = store.list_dictations(limit=2, offset=0)
    page2 = store.list_dictations(limit=2, offset=2)
    assert [r["text"] for r in page1] == ["item 4", "item 3"]
    assert [r["text"] for r in page2] == ["item 2", "item 1"]


def test_search_dictations(store):
    store.add_dictation("the quick brown fox", 1, None, None)
    store.add_dictation("a lazy dog sleeps", 1, None, None)
    results = store.list_dictations(query="fox")
    assert len(results) == 1
    assert "fox" in results[0]["text"]
    assert store.list_dictations(query="nonexistent") == []


def test_search_handles_special_characters_safely(store):
    store.add_dictation("what's up?", 1, None, None)
    # FTS5 special syntax chars (quotes, hyphens, colons) must not raise.
    for bad in ['"', "-foo", "foo:bar", "foo OR"]:
        store.list_dictations(query=bad)  # just must not raise


def test_delete_dictation(store):
    id_ = store.add_dictation("to delete", 1, None, None)
    store.delete_dictation(id_)
    assert store.list_dictations() == []


def test_delete_dictation_removes_it_from_search(store):
    id_ = store.add_dictation("searchable text", 1, None, None)
    store.delete_dictation(id_)
    assert store.list_dictations(query="searchable") == []


# --- stats ---


def test_stats_empty_store(store):
    stats = store.stats()
    assert stats == {
        "words_today": 0,
        "words_week": 0,
        "words_total": 0,
        "dictations_total": 0,
        "minutes_saved": 0.0,
    }


def test_stats_counts_words_and_dictations(store, clock):
    store.add_dictation("one two three", 3.0, None, None)  # 3 words
    store.add_dictation("four five", 2.0, None, None)  # 2 words
    stats = store.stats()
    assert stats["words_total"] == 5
    assert stats["dictations_total"] == 2
    assert stats["words_today"] == 5
    assert stats["words_week"] == 5


def test_stats_today_vs_older(store, clock):
    store.add_dictation("old words here", 1, None, None)  # 3 words, "today" at this point
    clock.now += timedelta(days=2)
    store.add_dictation("new one", 1, None, None)  # 2 words
    stats = store.stats()
    assert stats["words_today"] == 2
    assert stats["words_total"] == 5


def test_stats_week_excludes_older_than_7_days(store, clock):
    store.add_dictation("ancient text here now", 1, None, None)  # 4 words
    clock.now += timedelta(days=8)
    store.add_dictation("recent", 1, None, None)  # 1 word
    stats = store.stats()
    assert stats["words_week"] == 1
    assert stats["words_total"] == 5


def test_stats_minutes_saved_is_never_negative(store):
    # Speaking a single word for a very long time should not give a negative number.
    store.add_dictation("hi", 600.0, None, None)
    assert store.stats()["minutes_saved"] == 0.0


# --- meetings, speakers, segments ---


def test_create_and_get_meeting(store):
    mid = store.create_meeting(title="Standup", source_app="Zoom", attendees=["Alice", "Bob"])
    meeting = store.get_meeting(mid)
    assert meeting["title"] == "Standup"
    assert meeting["source_app"] == "Zoom"
    assert meeting["attendees"] == ["Alice", "Bob"]
    assert meeting["status"] == "recording"
    assert meeting["ended_at"] is None
    assert meeting["speakers"] == []
    assert meeting["segments"] == []


def test_get_missing_meeting_returns_none(store):
    assert store.get_meeting(999) is None


def test_finish_meeting(store):
    mid = store.create_meeting()
    store.finish_meeting(mid, "done")
    meeting = store.get_meeting(mid)
    assert meeting["status"] == "done"
    assert meeting["ended_at"] is not None


def test_set_meeting_title(store):
    mid = store.create_meeting(title="Untitled")
    store.set_meeting_title(mid, "Renamed")
    assert store.get_meeting(mid)["title"] == "Renamed"


def test_list_meetings_newest_first(store, clock):
    store.create_meeting(title="first")
    clock.now += timedelta(minutes=5)
    store.create_meeting(title="second")
    titles = [m["title"] for m in store.list_meetings()]
    assert titles == ["second", "first"]


def test_speakers_and_segments_round_trip(store):
    mid = store.create_meeting(title="1:1")
    you = store.add_speaker(mid, "You", display_name="Rylan", is_me=True)
    others = store.add_speaker(mid, "Others")

    store.add_segments(
        mid,
        [
            {"speaker_id": you, "track": "mic", "start_s": 0.0, "end_s": 2.0, "text": "hello"},
            {"speaker_id": others, "track": "system", "start_s": 2.5, "end_s": 4.0, "text": "hi"},
        ],
    )

    meeting = store.get_meeting(mid)
    assert len(meeting["speakers"]) == 2
    me = next(s for s in meeting["speakers"] if s["is_me"])
    assert me["display_name"] == "Rylan"
    assert [seg["text"] for seg in meeting["segments"]] == ["hello", "hi"]
    assert meeting["segments"][0]["speaker_id"] == you


def test_segments_ordered_by_start_time_even_if_inserted_out_of_order(store):
    mid = store.create_meeting()
    store.add_segments(
        mid,
        [
            {"speaker_id": None, "track": "mic", "start_s": 10.0, "end_s": 12.0, "text": "later"},
            {"speaker_id": None, "track": "mic", "start_s": 1.0, "end_s": 2.0, "text": "earlier"},
        ],
    )
    texts = [s["text"] for s in store.get_meeting(mid)["segments"]]
    assert texts == ["earlier", "later"]


def test_rename_speaker(store):
    mid = store.create_meeting()
    sid = store.add_speaker(mid, "Speaker 1")
    store.rename_speaker(sid, "Jordan")
    speaker = store.get_meeting(mid)["speakers"][0]
    assert speaker["display_name"] == "Jordan"


def test_reassign_segments_moves_them_to_a_new_speaker(store):
    mid = store.create_meeting()
    s1 = store.add_speaker(mid, "Speaker 1")
    s2 = store.add_speaker(mid, "Speaker 2")
    [seg_id] = store.add_segments(
        mid, [{"speaker_id": s1, "track": "system", "start_s": 0, "end_s": 1, "text": "hi"}]
    )
    store.reassign_segments({seg_id: s2})
    segment = store.get_meeting(mid)["segments"][0]
    assert segment["speaker_id"] == s2


def test_delete_meeting_cascades_to_speakers_and_segments(store):
    mid = store.create_meeting()
    speaker_id = store.add_speaker(mid, "You")
    store.add_segments(
        mid, [{"speaker_id": speaker_id, "track": "mic", "start_s": 0, "end_s": 1, "text": "hi"}]
    )
    store.delete_meeting(mid)
    assert store.get_meeting(mid) is None
    # Nothing should be left referencing the deleted meeting.
    raw = store._conn.execute("SELECT COUNT(*) FROM speakers").fetchone()[0]
    assert raw == 0
    raw = store._conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
    assert raw == 0


def test_delete_speaker_leaves_segments_with_null_speaker(store):
    mid = store.create_meeting()
    sid = store.add_speaker(mid, "Speaker 1")
    store.add_segments(
        mid, [{"speaker_id": sid, "track": "mic", "start_s": 0, "end_s": 1, "text": "hi"}]
    )
    store.delete_speaker(sid)
    segment = store.get_meeting(mid)["segments"][0]
    assert segment["speaker_id"] is None


# --- misc ---


def test_data_dir_honours_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("RYLANFLOW_DATA_DIR", str(tmp_path / "custom"))
    import importlib

    from rylanflow import store as store_module

    importlib.reload(store_module)
    assert store_module.data_dir() == tmp_path / "custom"
    assert store_module.default_db_path() == tmp_path / "custom" / "rylanflow.db"
    monkeypatch.delenv("RYLANFLOW_DATA_DIR", raising=False)
    importlib.reload(store_module)


def test_reopening_existing_database_does_not_fail(tmp_path, clock):
    path = tmp_path / "reopen.db"
    s1 = Store(path, clock=clock)
    s1.add_dictation("persisted", 1, None, None)
    s1.close()
    s2 = Store(path, clock=clock)
    assert [r["text"] for r in s2.list_dictations()] == ["persisted"]
    s2.close()
