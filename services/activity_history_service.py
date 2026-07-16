from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import core.config as config


DEFAULT_HISTORY_PATH = os.path.join(config.BASE_DIR, "state", "activity_history.sqlite3")
_DB_LOCK = threading.RLock()
_LOGGER = logging.getLogger(__name__)


class ActivityHistoryStore:
    """Append-only SQLite history used by overview statistics."""

    def __init__(self, path: str | None = None):
        self.path = Path(path or DEFAULT_HISTORY_PATH)
        self._ensure_schema()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with _DB_LOCK, self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS activity_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_activity_time "
                "ON activity_events(occurred_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_activity_video "
                "ON activity_events(profile_id, video_id)"
            )

    def record_event(
        self,
        profile_id: str,
        video_id: str,
        event_type: str,
        *,
        status: str = "",
        platform: str = "",
        error: str = "",
        occurred_at: str | None = None,
    ) -> None:
        try:
            timestamp = occurred_at or datetime.now().isoformat(timespec="seconds")
            with _DB_LOCK, self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO activity_events (
                        occurred_at, profile_id, video_id, event_type,
                        status, platform, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timestamp,
                        str(profile_id or ""),
                        str(video_id or ""),
                        str(event_type or ""),
                        str(status or ""),
                        str(platform or ""),
                        str(error or ""),
                    ),
                )
        except Exception as exc:
            _LOGGER.warning("Could not record activity history: %s", exc)

    def list_events_since(self, occurred_at: str) -> list[dict]:
        with _DB_LOCK, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT occurred_at, profile_id, video_id, event_type,
                       status, platform, error
                FROM activity_events
                WHERE occurred_at >= ?
                ORDER BY occurred_at ASC, id ASC
                """,
                (str(occurred_at),),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune(self, retention_days: int = 90, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now()) - timedelta(days=max(1, int(retention_days)))
        with _DB_LOCK, self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM activity_events WHERE occurred_at < ?",
                (cutoff.isoformat(timespec="seconds"),),
            )
            removed = max(0, int(cursor.rowcount or 0))
        if removed:
            try:
                with _DB_LOCK, self._connection() as connection:
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
                    connection.execute("VACUUM")
            except Exception as exc:
                _LOGGER.warning("Could not compact activity history: %s", exc)
        return removed
