import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from profile_automation.pipeline.video_job_store import VideoJobStore
from services.activity_history_service import ActivityHistoryStore
from services.runtime_maintenance_service import _clean_matching_files
from services.telegram_service import (
    _prune_caption_message_map,
    _prune_caption_requests,
)
import services.stats_service as stats_service


class VideoJobRetentionTests(unittest.TestCase):
    def test_prune_removes_old_terminal_jobs_but_keeps_failed_and_pending(self):
        now = datetime(2026, 7, 15, 12, 0, 0)
        old = (now - timedelta(days=31)).isoformat(timespec="seconds")
        recent = (now - timedelta(days=1)).isoformat(timespec="seconds")
        data = {
            "schema_version": 1,
            "jobs": {
                "1:old-complete": {"status": "completed", "updated_at": old},
                "1:old-cancelled": {"status": "cancelled", "updated_at": old},
                "1:old-failed": {"status": "failed_upload", "updated_at": old},
                "1:old-pending": {"status": "downloaded", "updated_at": old},
                "1:recent-complete": {"status": "completed", "updated_at": recent},
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "video_jobs.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            store = VideoJobStore(str(path))

            removed = store.prune_terminal_jobs(now=now)
            retained = json.loads(path.read_text(encoding="utf-8"))["jobs"]

        self.assertEqual(removed, 2)
        self.assertNotIn("1:old-complete", retained)
        self.assertNotIn("1:old-cancelled", retained)
        self.assertIn("1:old-failed", retained)
        self.assertIn("1:old-pending", retained)
        self.assertIn("1:recent-complete", retained)

    def test_prune_caps_recent_terminal_jobs(self):
        now = datetime(2026, 7, 15, 12, 0, 0)
        jobs = {
            f"1:{index}": {
                "status": "completed",
                "updated_at": (now - timedelta(minutes=index)).isoformat(timespec="seconds"),
            }
            for index in range(8)
        }
        data = {"schema_version": 1, "jobs": jobs}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "video_jobs.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            store = VideoJobStore(str(path))

            removed = store.prune_terminal_jobs(max_terminal_jobs=3, now=now)
            retained = json.loads(path.read_text(encoding="utf-8"))["jobs"]

        self.assertEqual(removed, 5)
        self.assertEqual(set(retained), {"1:0", "1:1", "1:2"})


class ActivityHistoryRetentionTests(unittest.TestCase):
    def test_prune_keeps_only_events_inside_retention_window(self):
        now = datetime(2026, 7, 15, 12, 0, 0)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ActivityHistoryStore(str(Path(temp_dir) / "history.sqlite3"))
            store.record_event(
                "1",
                "old",
                "completed",
                occurred_at=(now - timedelta(days=91)).isoformat(timespec="seconds"),
            )
            store.record_event(
                "1",
                "recent",
                "completed",
                occurred_at=(now - timedelta(days=2)).isoformat(timespec="seconds"),
            )

            removed = store.prune(retention_days=90, now=now)
            events = store.list_events_since("2000-01-01T00:00:00")

        self.assertEqual(removed, 1)
        self.assertEqual([event["video_id"] for event in events], ["recent"])


class TelegramRetentionTests(unittest.TestCase):
    def test_caption_prune_uses_separate_pending_and_terminal_retention(self):
        now = 1_000_000.0
        data = {
            "fresh-pending": {"status": "pending", "updated_at": now - 3600},
            "old-pending": {"status": "pending", "updated_at": now - 3 * 86400},
            "fresh-done": {"status": "resolved", "updated_at": now - 3 * 86400},
            "old-done": {"status": "resolved", "updated_at": now - 8 * 86400},
        }

        retained = _prune_caption_requests(data, now_timestamp=now)
        mappings = _prune_caption_message_map(
            {"10": "fresh-pending", "11": "old-pending", "12": "fresh-done"},
            retained.keys(),
        )

        self.assertEqual(set(retained), {"fresh-pending", "fresh-done"})
        self.assertEqual(mappings, {"12": "fresh-done", "10": "fresh-pending"})


class RuntimeFileCleanupTests(unittest.TestCase):
    def test_cleanup_removes_only_matching_old_temporary_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_part = root / "video.mp4.part"
            old_converted = root / "video_youtube_shorts.mp4"
            source_video = root / "video.mp4"
            fresh_part = root / "fresh.mp4.part"
            for path in (old_part, old_converted, source_video, fresh_part):
                path.write_bytes(b"data")
            old_timestamp = datetime.now().timestamp() - 3 * 86400
            os.utime(old_part, (old_timestamp, old_timestamp))
            os.utime(old_converted, (old_timestamp, old_timestamp))
            os.utime(source_video, (old_timestamp, old_timestamp))

            removed = _clean_matching_files(
                root,
                ("*.part", "*_youtube_shorts*.mp4"),
                datetime.now().timestamp() - 86400,
            )

            self.assertEqual(removed, 2)
            self.assertFalse(old_part.exists())
            self.assertFalse(old_converted.exists())
            self.assertTrue(source_video.exists())
            self.assertTrue(fresh_part.exists())


class StatsRetentionTests(unittest.TestCase):
    def test_prune_stats_history_rewrites_file_without_expired_days(self):
        today = datetime.now().date()
        old_day = (today - timedelta(days=401)).isoformat()
        current_day = today.isoformat()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stats.json"
            path.write_text(
                json.dumps(
                    {
                        "daily": {
                            old_day: {"scanned": 1, "uploaded": 0},
                            current_day: {"scanned": 2, "uploaded": 1},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(stats_service, "STATS_FILE", str(path)):
                removed = stats_service.prune_stats_history()
            retained = json.loads(path.read_text(encoding="utf-8"))["daily"]

        self.assertEqual(removed, 1)
        self.assertEqual(set(retained), {current_day})


if __name__ == "__main__":
    unittest.main()
