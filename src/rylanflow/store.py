"""SQLite storage for dictations and meetings, shared by the dashboard and meeting pipeline.

One `Store` instance holds a single connection (safe to call from any thread: every call is
serialized behind a lock) at `~/Library/Application Support/RylanFlow/rylanflow.db`, or
`RYLANFLOW_DATA_DIR` if set. WAL mode lets the dashboard read while a background thread writes.
"""

import json
import os
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

TYPING_WPM = 40  # assumed typing speed, for the "minutes saved" stat

_MIGRATIONS: list[str] = [
    # v1: dictations
    """
    CREATE TABLE dictations (
        id INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL,
        text TEXT NOT NULL,
        seconds REAL NOT NULL,
        app_name TEXT,
        model TEXT
    );
    CREATE INDEX dictations_created_at ON dictations(created_at);

    CREATE VIRTUAL TABLE dictations_fts USING fts5(text, content='dictations', content_rowid='id');

    CREATE TRIGGER dictations_ai AFTER INSERT ON dictations BEGIN
        INSERT INTO dictations_fts(rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TRIGGER dictations_ad AFTER DELETE ON dictations BEGIN
        INSERT INTO dictations_fts(dictations_fts, rowid, text) VALUES ('delete', old.id, old.text);
    END;
    """,
    # v2: meetings, speakers, segments
    """
    CREATE TABLE meetings (
        id INTEGER PRIMARY KEY,
        title TEXT,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        source_app TEXT,
        calendar_event_id TEXT,
        attendees_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'recording'
    );

    CREATE TABLE speakers (
        id INTEGER PRIMARY KEY,
        meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        label TEXT NOT NULL,
        display_name TEXT,
        is_me INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE segments (
        id INTEGER PRIMARY KEY,
        meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        speaker_id INTEGER REFERENCES speakers(id) ON DELETE SET NULL,
        track TEXT NOT NULL,
        start_s REAL NOT NULL,
        end_s REAL NOT NULL,
        text TEXT NOT NULL
    );
    CREATE INDEX segments_meeting ON segments(meeting_id, start_s);
    """,
    # v3: full-text search over meeting segments, same pattern as dictations_fts
    """
    CREATE VIRTUAL TABLE segments_fts USING fts5(text, content='segments', content_rowid='id');

    CREATE TRIGGER segments_ai AFTER INSERT ON segments BEGIN
        INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TRIGGER segments_ad AFTER DELETE ON segments BEGIN
        INSERT INTO segments_fts(segments_fts, rowid, text) VALUES ('delete', old.id, old.text);
    END;
    """,
    # v4: remembered voice prints, so a speaker named once in the dashboard can be
    # auto-recognized by voice in future meetings (meetings/speakers.match_voice).
    """
    CREATE TABLE voices (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        embedding_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
]


def data_dir() -> Path:
    override = os.environ.get("RYLANFLOW_DATA_DIR")
    path = Path(override) if override else Path("~/Library/Application Support/RylanFlow")
    return path.expanduser()


def default_db_path() -> Path:
    return data_dir() / "rylanflow.db"


def _now_iso(clock: Callable[[], datetime]) -> str:
    return clock().astimezone(UTC).isoformat()


class Store:
    """Thread-safe handle to the RylanFlow database. Create one and keep it for the app's life."""

    def __init__(
        self, path: Path | None = None, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for i, script in enumerate(_MIGRATIONS[version:], start=version + 1):
            self._conn.executescript(script)
            self._conn.execute(f"PRAGMA user_version={i}")
        self._conn.commit()

    # --- dictations ---

    def add_dictation(
        self, text: str, seconds: float, app_name: str | None, model: str | None
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO dictations (created_at, text, seconds, app_name, model) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now_iso(self._clock), text, seconds, app_name, model),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_dictations(
        self, limit: int = 50, offset: int = 0, query: str | None = None
    ) -> list[dict]:
        with self._lock:
            if query:
                rows = self._conn.execute(
                    "SELECT d.* FROM dictations d JOIN dictations_fts f ON f.rowid = d.id "
                    "WHERE dictations_fts MATCH ? ORDER BY d.created_at DESC LIMIT ? OFFSET ?",
                    (_fts_query(query), limit, offset),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM dictations ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            return [dict(r) for r in rows]

    def delete_dictation(self, dictation_id: int) -> bool:
        """Returns whether a row actually existed to delete."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM dictations WHERE id = ?", (dictation_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def stats(self) -> dict:
        with self._lock:
            now = self._clock().astimezone(UTC)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            week_start = (now - timedelta(days=7)).isoformat()
            words_today = self._word_count_since(today_start)
            words_week = self._word_count_since(week_start)
            total_row = self._conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(seconds), 0) AS secs FROM dictations"
            ).fetchone()
            words_total = self._word_count_since(None)
            typing_minutes = words_total / TYPING_WPM
            speaking_minutes = total_row["secs"] / 60
            return {
                "words_today": words_today,
                "words_week": words_week,
                "words_total": words_total,
                "dictations_total": total_row["n"],
                "minutes_saved": max(0.0, typing_minutes - speaking_minutes),
            }

    def _word_count_since(self, since_iso: str | None) -> int:
        if since_iso is None:
            rows = self._conn.execute("SELECT text FROM dictations").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT text FROM dictations WHERE created_at >= ?", (since_iso,)
            ).fetchall()
        return sum(len(r["text"].split()) for r in rows)

    # --- meetings ---

    def create_meeting(
        self,
        title: str | None = None,
        source_app: str | None = None,
        calendar_event_id: str | None = None,
        attendees: list[str] | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO meetings (title, started_at, source_app, calendar_event_id, "
                "attendees_json, status) VALUES (?, ?, ?, ?, ?, 'recording')",
                (
                    title,
                    _now_iso(self._clock),
                    source_app,
                    calendar_event_id,
                    json.dumps(attendees or []),
                ),
            )
            self._conn.commit()
            return cur.lastrowid

    def finish_meeting(self, meeting_id: int, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE meetings SET ended_at = ?, status = ? WHERE id = ?",
                (_now_iso(self._clock), status, meeting_id),
            )
            self._conn.commit()

    def set_meeting_title(self, meeting_id: int, title: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE meetings SET title = ? WHERE id = ?", (title, meeting_id))
            self._conn.commit()

    def set_meeting_attendees(self, meeting_id: int, attendees: list[str]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE meetings SET attendees_json = ? WHERE id = ?",
                (json.dumps(attendees), meeting_id),
            )
            self._conn.commit()

    def list_meetings(self, query: str | None = None) -> list[dict]:
        with self._lock:
            if query:
                # A subquery rather than a JOIN, so a meeting with zero segments but a matching
                # title (e.g. a freshly started, still-empty meeting) isn't excluded.
                rows = self._conn.execute(
                    "SELECT * FROM meetings WHERE "
                    "id IN (SELECT meeting_id FROM segments WHERE id IN "
                    "(SELECT rowid FROM segments_fts WHERE segments_fts MATCH ?)) "
                    "OR title LIKE ? "
                    "ORDER BY started_at DESC",
                    (_fts_query(query), f"%{query}%"),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM meetings ORDER BY started_at DESC"
                ).fetchall()
            return [_meeting_dict(r) for r in rows]

    def get_meeting(self, meeting_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM meetings WHERE id = ?", (meeting_id,)
            ).fetchone()
            if row is None:
                return None
            meeting = _meeting_dict(row)
            meeting["speakers"] = [
                dict(r)
                for r in self._conn.execute(
                    "SELECT * FROM speakers WHERE meeting_id = ? ORDER BY id", (meeting_id,)
                ).fetchall()
            ]
            meeting["segments"] = [
                dict(r)
                for r in self._conn.execute(
                    "SELECT * FROM segments WHERE meeting_id = ? ORDER BY start_s", (meeting_id,)
                ).fetchall()
            ]
            return meeting

    def delete_meeting(self, meeting_id: int) -> bool:
        """Returns whether a row actually existed to delete."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # --- speakers & segments ---

    def add_speaker(
        self, meeting_id: int, label: str, display_name: str | None = None, is_me: bool = False
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO speakers (meeting_id, label, display_name, is_me) VALUES (?, ?, ?, ?)",
                (meeting_id, label, display_name, int(is_me)),
            )
            self._conn.commit()
            return cur.lastrowid

    def rename_speaker(self, speaker_id: int, display_name: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE speakers SET display_name = ? WHERE id = ?", (display_name, speaker_id)
            )
            self._conn.commit()

    def get_speaker(self, speaker_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM speakers WHERE id = ?", (speaker_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_speaker(self, speaker_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))
            self._conn.commit()

    def add_segments(self, meeting_id: int, rows: list[dict]) -> list[int]:
        """Each row: {speaker_id, track, start_s, end_s, text}. Returns the new segment ids."""
        with self._lock:
            ids = []
            for row in rows:
                cur = self._conn.execute(
                    "INSERT INTO segments (meeting_id, speaker_id, track, start_s, end_s, text) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        meeting_id,
                        row.get("speaker_id"),
                        row["track"],
                        row["start_s"],
                        row["end_s"],
                        row["text"],
                    ),
                )
                ids.append(cur.lastrowid)
            self._conn.commit()
            return ids

    def reassign_segments(self, assignments: dict[int, int]) -> None:
        """Move each segment id to a new speaker id: {segment_id: speaker_id}."""
        with self._lock:
            self._conn.executemany(
                "UPDATE segments SET speaker_id = ? WHERE id = ?",
                [(speaker_id, segment_id) for segment_id, speaker_id in assignments.items()],
            )
            self._conn.commit()

    # --- voices (remembered voice prints, step 3.9) ---

    def save_voice(self, name: str, embedding: list[float]) -> None:
        """Upserts by name -- naming the same person again just updates their stored voice."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO voices (name, embedding_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "embedding_json = excluded.embedding_json, updated_at = excluded.updated_at",
                (name, json.dumps(embedding), _now_iso(self._clock)),
            )
            self._conn.commit()

    def list_voices(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT name, embedding_json FROM voices").fetchall()
            return [{"name": r["name"], "embedding": json.loads(r["embedding_json"])} for r in rows]


def _meeting_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["attendees"] = json.loads(d.pop("attendees_json"))
    return d


def _fts_query(text: str) -> str:
    """Quote the user's text as an FTS5 phrase so punctuation can't break the query syntax."""
    return '"' + text.replace('"', '""') + '"'
