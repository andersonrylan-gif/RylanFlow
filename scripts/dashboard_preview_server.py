"""Dev-only helper: run the dashboard against seeded sample data for manual/visual testing.

Not part of the app; not imported anywhere. Usage: uv run python scripts/dashboard_preview_server.py
"""

import time
from datetime import UTC, datetime, timedelta

from rylanflow.dashboard.server import DashboardServer
from rylanflow.store import Store


class FakeActions:
    def get_settings(self):
        return {"hotkey": "alt_r", "model": "base", "sounds": True, "remove_fillers": True}

    def apply_settings(self, changes):
        pass


def main():
    store = Store(clock=lambda: datetime.now(UTC))
    now = datetime.now(UTC)
    samples = [
        (
            now,
            "Hey team, just a quick update on the RylanFlow dashboard — it's coming along nicely.",
            "Slack",
            "base",
        ),
        (
            now - timedelta(minutes=12),
            "Remember to pick up milk, eggs, and that good sourdough from the bakery on Fifth.",
            "Notes",
            "base",
        ),
        (
            now - timedelta(hours=2),
            "This is a longer dictation meant to test the three line clamp behavior in the "
            "dashboard card component, which should truncate and let you click to expand it.",
            "VS Code",
            "large-v3-turbo",
        ),
        (
            now - timedelta(days=1, hours=1),
            "Yesterday's standup notes: finished the store, started on the dashboard server.",
            None,
            "base",
        ),
        (
            now - timedelta(days=3),
            "A few days ago I dictated this note about the quarterly roadmap.",
            "Notes",
            "base",
        ),
    ]
    for created, text, app_name, model in samples:
        store._conn.execute(
            "INSERT INTO dictations (created_at, text, seconds, app_name, model) "
            "VALUES (?, ?, ?, ?, ?)",
            (created.isoformat(), text, len(text.split()) / 2.5, app_name, model),
        )
    store._conn.commit()

    srv = DashboardServer(store, FakeActions())
    print(srv.start(), flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
