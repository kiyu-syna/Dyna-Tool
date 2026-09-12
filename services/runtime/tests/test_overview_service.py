import os
import tempfile
import unittest
from datetime import datetime

from application.workflows.video_job_store import VideoJobStore
from profile_automation.watchers.douyin_profile_monitor import DouyinVideo
from services.runtime.activity_history_service import ActivityHistoryStore
from services.runtime.overview_service import build_overview_snapshot


def make_video(video_id: str) -> DouyinVideo:
    return DouyinVideo(
        aweme_id=video_id,
        share_url=f"https://www.douyin.com/video/{video_id}",
        desc="Test",
        create_time=100,
        duration_ms=10000,
        like_count=1,
        play_count=1,
        author_uid="author",
        author_nickname="Tester",
    )


class OverviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.jobs = VideoJobStore(os.path.join(self.temp_dir.name, "jobs.json"))
        self.history = ActivityHistoryStore(os.path.join(self.temp_dir.name, "history.sqlite3"))
        self.jobs.history = self.history
        self.profiles = {
            "90": {"id": "90", "name": "Alpha", "enabled": True, "tiktok": {"enabled": True}},
            "91": {"id": "91", "name": "Beta", "enabled": True, "tiktok": {"enabled": True}},
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_history_store_records_events(self):
        self.history.record_event(
            "90",
            "100",
            "status",
            status="completed",
            occurred_at="2026-07-15T10:00:00",
        )

        events = self.history.list_events_since("2026-07-15T00:00:00")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "completed")

    def test_snapshot_aggregates_kpis_platforms_profiles_and_errors(self):
        first = make_video("100")
        second = make_video("200")
        third = make_video("300")
        fourth = make_video("400")
        self.jobs.ensure_job("90", first, self.profiles["90"])
        self.jobs.ensure_job("90", second, self.profiles["90"])
        self.jobs.ensure_job("91", third, self.profiles["91"])
        self.jobs.ensure_job("91", fourth, self.profiles["91"])
        self.jobs.set_platform_status("90", "100", "tiktok", "success")
        self.jobs.set_status("90", "100", "completed")
        self.jobs.set_status("90", "200", "failed_download", error="Download failed")
        self.jobs.set_status("91", "300", "waiting_caption")
        self.jobs.set_status("91", "400", "downloading")

        snapshot = build_overview_snapshot(
            job_store=self.jobs,
            history_store=self.history,
            profiles=self.profiles,
            active_profile_ids={"90"},
            now=datetime.now(),
        )

        self.assertEqual(snapshot["kpis"]["detected_today"], 4)
        self.assertEqual(snapshot["kpis"]["completed_today"], 1)
        self.assertEqual(snapshot["kpis"]["waiting"], 2)
        self.assertEqual(snapshot["kpis"]["errors"], 1)
        self.assertEqual(snapshot["kpis"]["success_rate"], 50)
        tiktok = next(item for item in snapshot["platforms"] if item["key"] == "tiktok")
        self.assertEqual(tiktok["success"], 1)
        self.assertEqual(tiktok["pending"], 3)
        alpha = next(item for item in snapshot["profiles"] if item["profile_id"] == "90")
        self.assertTrue(alpha["running"])
        self.assertEqual(alpha["errors"], 1)
        self.assertEqual(snapshot["errors"][0]["video_id"], "200")


if __name__ == "__main__":
    unittest.main()
