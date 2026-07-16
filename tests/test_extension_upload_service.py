import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from profile_automation.pipeline.profile_worker import ProfileWorker
from profile_automation.pipeline.video_job_store import VideoJobStore
from services.extension_upload_service import ExtensionUploadRequest, ExtensionUploadService
from services.profile_management_service import ProfileManagementService


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
            "services.extension_upload_service.get_tracking_download_path",
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
            "services.extension_upload_service.get_tracking_download_path",
            return_value=str(destination),
        ), patch(
            "services.extension_upload_service.ProfileWorker.process_video",
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


if __name__ == "__main__":
    unittest.main()
