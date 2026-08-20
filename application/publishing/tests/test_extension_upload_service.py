import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from application.workflows.profile_worker import ProfileWorker
from application.workflows.video_job_store import VideoJobStore
from application.publishing.extension_upload_service import (
    LOCAL_SOURCE_KEY,
    ExtensionUploadRequest,
    ExtensionUploadService,
)
from application.tracking.profile_management_service import ProfileManagementService


class ExtensionUploadServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profiles = ProfileManagementService(
            self.root / "profiles",
            self.root / "state",
        )
        profile = self.profiles.create("2", "Profile 2")
        profile["tiktok"]["enabled"] = True
        self.profiles.save("2", profile)
        self.jobs = VideoJobStore(str(self.root / "video_jobs.json"))
        self.service = ExtensionUploadService(self.jobs, self.profiles)
        self.video_path = self.root / "7662.mp4"
        self.video_path.write_bytes(b"video-data")

    def tearDown(self):
        self.temp.cleanup()

    def request(self, **changes):
        values = {
            "profile_id": "2",
            "video_id": "7662",
            "file_path": str(self.video_path),
            "source_url": "https://www.douyin.com/video/7662",
            "description": "Test video",
        }
        values.update(changes)
        return ExtensionUploadRequest(**values)

    def test_profile_list_reports_enabled_platforms(self):
        profiles = self.service.list_profiles()

        self.assertEqual(len(profiles), 1)
        self.assertTrue(profiles[0]["available"])
        self.assertTrue(profiles[0]["platforms"]["tiktok"])

    def test_submit_copies_file_before_accepting_job(self):
        destination = self.root / "managed" / "7662.mp4"
        with patch(
            "application.publishing.extension_upload_service.get_tracking_download_path",
            return_value=str(destination),
        ), patch.object(self.service, "_start_processing", return_value=True) as start_processing:
            result = self.service.submit(self.request())

        self.assertTrue(result["accepted"])
        self.assertEqual(destination.read_bytes(), b"video-data")
        self.assertEqual(self.jobs.get_job("2", "7662")["status"], "downloaded")
        start_processing.assert_called_once()

    def test_import_copies_browser_file_before_running_worker(self):
        request = self.request()
        video = self.service._video_from_request(request)
        profile = self.profiles.load("2")
        source = {
            "source_key": "dyna_browser_extension",
            "target_sec_uid": "extension",
            "target_display_name": "Trình duyệt Douyin",
        }
        worker = ProfileWorker(profile)
        worker.job_store = self.jobs
        worker.register_video(video, source)
        destination = self.root / "managed" / "7662.mp4"

        with patch(
            "application.publishing.extension_upload_service.get_tracking_download_path",
            return_value=str(destination),
        ), patch(
            "application.publishing.extension_upload_service.ProfileWorker.process_video",
            return_value={"status": "completed", "results": {}},
        ) as process_video:
            self.service._copy_into_managed_storage("2", "7662", self.video_path)
            self.service._process_managed_upload("2", profile, video, source, self.video_path)

        self.assertEqual(destination.read_bytes(), b"video-data")
        self.assertEqual(self.jobs.get_job("2", "7662")["download_path"], str(destination))
        process_video.assert_called_once()

    def test_resume_pending_extension_job_with_managed_file(self):
        request = self.request()
        video = self.service._video_from_request(request)
        profile = self.profiles.load("2")
        source = {
            "source_key": "dyna_browser_extension",
            "target_sec_uid": "extension",
            "target_display_name": "Trình duyệt Douyin",
        }
        worker = ProfileWorker(profile)
        worker.job_store = self.jobs
        worker.register_video(video, source)
        self.jobs.set_download_path("2", "7662", str(self.video_path))

        with patch.object(self.service, "_start_processing", return_value=True) as start_processing:
            resumed = self.service.resume_pending_jobs()

        self.assertEqual(resumed, 1)
        start_processing.assert_called_once()

    def test_rejects_non_douyin_source_url(self):
        with self.assertRaisesRegex(ValueError, "Douyin"):
            self.service.submit(
                self.request(source_url="https://example.com/video/7662")
            )

    def test_local_publish_uses_supplied_caption_and_selected_platforms(self):
        destination = self.root / "managed" / "local_1.mp4"
        request = self.request(
            video_id="local_1",
            source_url="",
            description="Caption local",
            source_key=LOCAL_SOURCE_KEY,
            source_label="Publish Center · 7662.mp4",
            platforms=("tiktok",),
            use_supplied_caption=True,
        )
        with patch(
            "application.publishing.extension_upload_service.get_tracking_download_path",
            return_value=str(destination),
        ), patch.object(self.service, "_start_processing", return_value=True):
            self.service.submit(request)

        job = self.jobs.get_job("2", "local_1")
        self.assertEqual(job["source_key"], LOCAL_SOURCE_KEY)
        self.assertEqual(job["caption"], "Caption local")
        self.assertTrue(job["caption_resolved"])
        self.assertEqual(job["enabled_platforms"], ["tiktok"])

    def test_scheduled_local_job_is_not_resumed_before_scheduler(self):
        destination = self.root / "managed" / "local_schedule.mp4"
        request = self.request(
            video_id="local_schedule",
            source_url="",
            description="Scheduled caption",
            source_key=LOCAL_SOURCE_KEY,
            source_label="Publish Center · scheduled.mp4",
            platforms=("tiktok",),
            use_supplied_caption=True,
            scheduled_at="2099-01-01T00:00:00Z",
        )
        with patch(
            "application.publishing.extension_upload_service.get_tracking_download_path",
            return_value=str(destination),
        ), patch.object(self.service, "_start_processing", return_value=True) as start:
            result = self.service.submit(request)
            resumed = self.service.resume_pending_jobs()
            started = self.service.start_scheduled_job("2", "local_schedule")

        self.assertEqual(result["job"]["status"], "scheduled")
        self.assertEqual(resumed, 0)
        self.assertEqual(started["status"], "downloaded")
        start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
