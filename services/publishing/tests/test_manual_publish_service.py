import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from profile_automation.pipeline.video_job_store import VideoJobStore
from services.publishing.extension_upload_service import LOCAL_SOURCE_KEY, ExtensionUploadService
from services.publishing.manual_publish_service import (
    ManualPublishItem,
    ManualPublishRequest,
    ManualPublishService,
    ManualPublishTarget,
)
from services.profiles.profile_management_service import ProfileManagementService


class ManualPublishServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profiles = ProfileManagementService(self.root / "profiles", self.root / "state")
        profile = self.profiles.create("2", "Creator")
        profile["tiktok"]["enabled"] = True
        profile["youtube"]["enabled"] = True
        self.profiles.save("2", profile)
        self.ingest = ExtensionUploadService(
            VideoJobStore(str(self.root / "jobs.json")),
            self.profiles,
        )
        self.ready_check = Mock()
        self.ready_check.profile_states.return_value = {}
        self.ready_check.ensure_ready.return_value = {
            "ready": True,
            "message": "Sẵn sàng đăng.",
            "checks": [],
        }
        self.service = ManualPublishService(self.ingest, self.ready_check)
        self.video_a = self.root / "a.mp4"
        self.video_b = self.root / "b.mov"
        self.video_a.write_bytes(b"video-a")
        self.video_b.write_bytes(b"video-b")

    def tearDown(self):
        self.temp.cleanup()

    def test_submit_creates_one_job_per_file_and_profile(self):
        request = ManualPublishRequest(
            file_paths=(str(self.video_a), str(self.video_b)),
            caption="Caption local",
            targets=(ManualPublishTarget("2", ("tiktok", "youtube")),),
        )
        with patch.object(
            self.ingest,
            "submit",
            side_effect=lambda item: {
                "accepted": True,
                "job": {"profile_id": item.profile_id, "video_id": item.video_id},
            },
        ) as submit:
            result = self.service.submit(request)

        self.assertEqual(result["file_count"], 2)
        self.assertEqual(result["job_count"], 2)
        first, second = [call.args[0] for call in submit.call_args_list]
        self.assertNotEqual(first.video_id, second.video_id)
        self.assertEqual(first.description, "Caption local")
        self.assertEqual(first.platforms, ("tiktok", "youtube"))
        self.assertEqual(first.source_key, LOCAL_SOURCE_KEY)
        self.assertTrue(first.use_supplied_caption)

    def test_submit_rejects_platform_not_enabled_on_profile(self):
        request = ManualPublishRequest(
            file_paths=(str(self.video_a),),
            caption="Caption",
            targets=(ManualPublishTarget("2", ("facebook",)),),
        )
        with self.assertRaisesRegex(ValueError, "chưa bật nền tảng"):
            self.service.submit(request)

    def test_each_video_keeps_its_own_caption_and_schedule(self):
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        request = ManualPublishRequest(
            items=(
                ManualPublishItem(str(self.video_a), "Caption A"),
                ManualPublishItem(str(self.video_b), "Caption B", future.isoformat()),
            ),
            targets=(ManualPublishTarget("2", ("tiktok",)),),
        )
        with patch.object(
            self.ingest,
            "submit",
            side_effect=lambda item: {"accepted": True, "job": {}},
        ) as submit:
            result = self.service.submit(request)

        first, second = [call.args[0] for call in submit.call_args_list]
        self.assertEqual(first.description, "Caption A")
        self.assertEqual(first.scheduled_at, "")
        self.assertEqual(second.description, "Caption B")
        self.assertTrue(second.scheduled_at.endswith("Z"))
        self.assertEqual(result["immediate_count"], 1)
        self.assertEqual(result["scheduled_count"], 1)

    def test_scheduler_starts_only_jobs_that_are_due(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        destination = self.root / "managed.mp4"
        request = ManualPublishRequest(
            items=(ManualPublishItem(str(self.video_a), "Caption", future.isoformat()),),
            targets=(ManualPublishTarget("2", ("tiktok",)),),
        )
        with patch(
            "services.publishing.extension_upload_service.get_tracking_download_path",
            return_value=str(destination),
        ):
            result = self.service.submit(request)

        job = result["jobs"][0]["job"]
        self.assertEqual(job["status"], "scheduled")
        with patch.object(self.ingest, "start_scheduled_job", return_value=job) as start:
            self.assertEqual(
                self.service.run_due_jobs(now=future - timedelta(seconds=1)),
                0,
            )
            self.assertEqual(
                self.service.run_due_jobs(now=future + timedelta(seconds=1)),
                1,
            )
        start.assert_called_once_with("2", job["video_id"])

    def test_submit_is_blocked_when_ready_check_fails(self):
        self.ready_check.ensure_ready.return_value = {
            "ready": False,
            "message": "TikTok đã hết phiên đăng nhập.",
            "checks": [],
        }
        request = ManualPublishRequest(
            items=(ManualPublishItem(str(self.video_a), "Caption"),),
            targets=(ManualPublishTarget("2", ("tiktok",)),),
        )

        with self.assertRaisesRegex(RuntimeError, "hết phiên đăng nhập"):
            self.service.submit(request)

    def test_due_job_waits_when_account_is_not_ready(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        destination = self.root / "managed-waiting.mp4"
        request = ManualPublishRequest(
            items=(ManualPublishItem(str(self.video_a), "Caption", future.isoformat()),),
            targets=(ManualPublishTarget("2", ("tiktok",)),),
        )
        with patch(
            "services.publishing.extension_upload_service.get_tracking_download_path",
            return_value=str(destination),
        ):
            result = self.service.submit(request)

        job = result["jobs"][0]["job"]
        self.ready_check.ensure_ready.return_value = {
            "ready": False,
            "message": "TikTok đã hết phiên đăng nhập.",
            "checks": [],
        }
        with patch.object(self.ingest, "start_scheduled_job") as start:
            self.assertEqual(self.service.run_due_jobs(now=future + timedelta(seconds=1)), 0)

        start.assert_not_called()
        waiting = self.ingest.jobs.get_job("2", job["video_id"])
        self.assertEqual(waiting["status"], "scheduled")
        self.assertIn("kiểm tra sẵn sàng", waiting["last_error"])

    def test_scheduler_preflights_ready_check_ten_minutes_early(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        destination = self.root / "managed-preflight.mp4"
        request = ManualPublishRequest(
            items=(ManualPublishItem(str(self.video_a), "Caption", future.isoformat()),),
            targets=(ManualPublishTarget("2", ("tiktok",)),),
        )
        with patch(
            "services.publishing.extension_upload_service.get_tracking_download_path",
            return_value=str(destination),
        ):
            result = self.service.submit(request)

        job = result["jobs"][0]["job"]
        self.ready_check.ensure_ready.reset_mock()
        with patch.object(self.ingest, "start_scheduled_job") as start:
            self.assertEqual(
                self.service.run_due_jobs(now=future - timedelta(minutes=9)),
                0,
            )

        self.ready_check.ensure_ready.assert_called_once_with(
            "2",
            ("tiktok",),
            force=False,
        )
        start.assert_not_called()
        self.assertEqual(
            self.ingest.jobs.get_job("2", job["video_id"])["status"],
            "scheduled",
        )

    def test_delete_hides_scheduled_job_and_preserves_original_video(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        destination = self.root / "managed-delete.mp4"
        request = ManualPublishRequest(
            items=(ManualPublishItem(str(self.video_a), "Caption", future.isoformat()),),
            targets=(ManualPublishTarget("2", ("tiktok",)),),
        )
        with patch(
            "services.publishing.extension_upload_service.get_tracking_download_path",
            return_value=str(destination),
        ):
            result = self.service.submit(request)

        job = result["jobs"][0]["job"]
        deleted = self.service.delete("2", job["video_id"])

        self.assertTrue(deleted["dismissed_at"])
        self.assertEqual(self.service.list_jobs()["total"], 0)
        self.assertTrue(self.video_a.is_file())
        self.assertTrue(destination.is_file())


if __name__ == "__main__":
    unittest.main()
