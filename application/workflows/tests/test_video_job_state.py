import os
import tempfile
import unittest
from unittest.mock import patch

from application.workflows.profile_worker import ProfileWorker
from application.publishing.upload_pipeline import (
    UploadPipeline,
    all_enabled_uploads_succeeded,
    enabled_platform_names,
)
from application.workflows.video_job_store import VideoJobStore
from profile_automation.uploaders.base_uploader import UploadSkipped
from profile_automation.watchers.douyin_video import DouyinVideo


def make_video(video_id: str = "123") -> DouyinVideo:
    return DouyinVideo(
        aweme_id=video_id,
        share_url=f"https://www.douyin.com/video/{video_id}",
        desc="Mô tả thử nghiệm",
        create_time=100,
        duration_ms=12_000,
        like_count=10,
        play_count=20,
        author_uid="author",
        author_nickname="Tester",
    )


class FakeUploader:
    def __init__(self, result=True):
        self.result = result
        self.calls = 0

    def upload(self, video_path, video, profile):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        if isinstance(self.result, list):
            return self.result.pop(0)
        return self.result


class FakeMonitor:
    def __init__(self):
        self.processed = []
        self.ignored = []

    def mark_processed(self, video):
        self.processed.append(video.aweme_id)

    def mark_ignored(self, video):
        self.ignored.append(video.aweme_id)


class VideoJobStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_path = os.path.join(self.temp_dir.name, "video_jobs.json")
        self.store = VideoJobStore(self.store_path)
        self.profile = {
            "tiktok": {"enabled": True},
            "facebook": {"enabled": True},
            "youtube": {"enabled": False},
        }
        self.video = make_video()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_job_excludes_the_source_platform_from_destinations(self):
        job = self.store.ensure_job(
            "1",
            self.video,
            self.profile,
            source_platform="tiktok",
        )

        self.assertEqual(job["source_platform"], "tiktok")
        self.assertEqual(job["enabled_platforms"], ["facebook"])
        self.assertEqual(job["platforms"]["tiktok"]["status"], "disabled")
        self.assertEqual(job["platforms"]["facebook"]["status"], "pending")

    def test_job_persists_caption_download_and_platform_status(self):
        self.store.ensure_job("1", self.video, self.profile, "source-a", "Nguồn A")
        self.store.set_caption("1", self.video.aweme_id, "Caption #tag")
        self.store.set_download_path("1", self.video.aweme_id, "C:/video.mp4")
        self.store.set_platform_status("1", self.video.aweme_id, "tiktok", "uploading")
        self.store.set_platform_status("1", self.video.aweme_id, "tiktok", "success")

        reloaded = VideoJobStore(self.store_path)
        job = reloaded.get_job("1", self.video.aweme_id)
        self.assertEqual(job["caption"], "Caption #tag")
        self.assertTrue(job["caption_resolved"])
        self.assertEqual(job["download_path"], os.path.normpath("C:/video.mp4"))
        self.assertEqual(job["platforms"]["tiktok"]["status"], "success")
        self.assertEqual(job["platforms"]["tiktok"]["attempts"], 1)
        self.assertEqual(reloaded.list_pending_videos("1", "source-a")[0].aweme_id, "123")

    def test_job_persists_telegram_caption_request_until_caption_arrives(self):
        self.store.ensure_job("1", self.video, self.profile)

        self.store.set_caption_request("1", self.video.aweme_id, "caption-request-1")

        waiting = VideoJobStore(self.store_path).get_job("1", self.video.aweme_id)
        self.assertEqual(waiting["status"], "waiting_caption")
        self.assertEqual(waiting["caption_request_id"], "caption-request-1")
        self.assertFalse(waiting["caption_resolved"])
        self.assertEqual(waiting["attempts"]["caption"], 1)

        self.store.set_caption("1", self.video.aweme_id, "Caption từ Telegram")
        selected = self.store.get_job("1", self.video.aweme_id)
        self.assertEqual(selected["status"], "caption_ready")
        self.assertEqual(selected["caption"], "Caption từ Telegram")
        self.assertEqual(selected["caption_request_id"], "")

    def test_recovery_keeps_unanswered_telegram_caption_request(self):
        self.store.ensure_job("1", self.video, self.profile)
        self.store.set_caption_request("1", self.video.aweme_id, "caption-request-1")
        self.assertTrue(self.store.claim("1", self.video.aweme_id))

        recovered = self.store.recover_interrupted_jobs()
        job = self.store.get_job("1", self.video.aweme_id)

        self.assertEqual(recovered["jobs"], 1)
        self.assertEqual(job["status"], "waiting_caption")
        self.assertEqual(job["caption_request_id"], "caption-request-1")
        self.assertFalse(job["active"])

    def test_claim_prevents_duplicate_processing_and_can_be_released(self):
        self.store.ensure_job("1", self.video, self.profile)
        self.assertTrue(self.store.claim("1", self.video.aweme_id))
        self.assertFalse(self.store.claim("1", self.video.aweme_id))
        self.store.release("1", self.video.aweme_id)
        self.assertTrue(self.store.claim("1", self.video.aweme_id))

    def test_failed_job_can_be_retried_or_cancelled(self):
        self.store.ensure_job("1", self.video, self.profile)
        self.store.set_caption("1", self.video.aweme_id, "Caption #tag")
        self.store.set_download_path("1", self.video.aweme_id, "C:/video.mp4")
        self.store.set_platform_status("1", self.video.aweme_id, "tiktok", "success")
        self.store.set_platform_status("1", self.video.aweme_id, "facebook", "failed", "Upload failed")
        self.store.set_status("1", self.video.aweme_id, "failed_upload", error="Đăng thất bại")

        self.assertEqual(self.store.list_pending_videos("1"), [])
        self.assertFalse(self.store.claim("1", self.video.aweme_id))

        retried = self.store.retry_job("1", self.video.aweme_id)
        self.assertEqual(retried["status"], "downloaded")
        self.assertEqual(retried["platforms"]["tiktok"]["status"], "success")
        self.assertEqual(retried["platforms"]["facebook"]["status"], "pending")
        self.assertEqual(retried["last_error"], "")
        self.assertEqual(self.store.list_pending_videos("1")[0].aweme_id, self.video.aweme_id)
        self.assertTrue(self.store.claim("1", self.video.aweme_id))
        self.store.release("1", self.video.aweme_id)

        cancelled = self.store.cancel_job("1", self.video.aweme_id, "User cancelled")
        self.assertEqual(cancelled["status"], "cancelled")

    def test_active_upload_can_be_cancelled(self):
        self.store.ensure_job("1", self.video, self.profile)
        self.store.set_status("1", self.video.aweme_id, "uploading")
        self.assertTrue(self.store.claim("1", self.video.aweme_id))

        cancelled = self.store.cancel_job("1", self.video.aweme_id, "User cancelled")

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(cancelled["active"])

    def test_dismissed_job_is_hidden_and_cannot_be_claimed_or_recreated(self):
        self.store.ensure_job("1", self.video, self.profile, "source-a", "Nguồn A")

        dismissed = self.store.dismiss_job("1", self.video.aweme_id)

        self.assertEqual(dismissed["status"], "ignored")
        self.assertTrue(dismissed["dismissed_at"])
        self.assertEqual(self.store.list_jobs(), [])
        self.assertFalse(self.store.claim("1", self.video.aweme_id))

        recreated = self.store.ensure_job("1", self.video, self.profile, "source-a", "Nguồn A")
        self.assertFalse(recreated["dismissed_at"])
        self.assertEqual(recreated["status"], "detected")
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_active_job_cannot_be_dismissed(self):
        self.store.ensure_job("1", self.video, self.profile)
        self.assertTrue(self.store.claim("1", self.video.aweme_id))

        with self.assertRaisesRegex(RuntimeError, "đang được xử lý"):
            self.store.dismiss_job("1", self.video.aweme_id)

        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_reset_queue_hides_every_inactive_job_atomically(self):
        second_video = make_video("456")
        self.store.ensure_job("1", self.video, self.profile)
        self.store.ensure_job("2", second_video, self.profile)

        result = self.store.dismiss_jobs(reason="Reset test")

        self.assertEqual(result["reset_count"], 2)
        self.assertEqual(self.store.list_jobs(), [])
        self.assertEqual(len(self.store.list_jobs(include_dismissed=True)), 2)

    def test_reset_queue_refuses_to_partially_clear_when_a_job_is_active(self):
        second_video = make_video("456")
        self.store.ensure_job("1", self.video, self.profile)
        self.store.ensure_job("2", second_video, self.profile)
        self.assertTrue(self.store.claim("2", second_video.aweme_id))

        with self.assertRaisesRegex(RuntimeError, "dừng Profile"):
            self.store.dismiss_jobs()

        self.assertEqual(len(self.store.list_jobs()), 2)

    def test_retry_preserves_platform_skipped_for_copyright(self):
        profile = {
            "tiktok": {"enabled": True},
            "facebook": {"enabled": False},
            "youtube": {"enabled": True},
        }
        self.store.ensure_job("1", self.video, profile)
        self.store.set_platform_status(
            "1",
            self.video.aweme_id,
            "youtube",
            "skipped",
            "YouTube phát hiện nội dung bản quyền.",
        )
        self.store.set_platform_status(
            "1", self.video.aweme_id, "tiktok", "failed", "Upload failed"
        )
        self.store.set_status("1", self.video.aweme_id, "failed_upload", error="Đăng thất bại")

        retried = self.store.retry_job("1", self.video.aweme_id)

        self.assertEqual(retried["platforms"]["youtube"]["status"], "skipped")
        self.assertEqual(retried["platforms"]["tiktok"]["status"], "pending")

    def test_interrupted_upload_is_recovered_for_resume(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as video_file:
            video_file.write(b"video" * 300)
            video_path = video_file.name
        try:
            self.store.ensure_job("1", self.video, self.profile)
            self.store.set_caption("1", self.video.aweme_id, "Caption #tag")
            self.store.set_download_path("1", self.video.aweme_id, video_path)
            self.store.set_platform_status("1", self.video.aweme_id, "tiktok", "uploading")
            self.store.set_status("1", self.video.aweme_id, "uploading")
            self.assertTrue(self.store.claim("1", self.video.aweme_id))

            recovered = self.store.recover_interrupted_jobs()
            job = self.store.get_job("1", self.video.aweme_id)

            self.assertEqual(recovered["jobs"], 1)
            self.assertEqual(recovered["profile_ids"], ["1"])
            self.assertEqual(job["status"], "downloaded")
            self.assertEqual(job["platforms"]["tiktok"]["status"], "pending")
            self.assertFalse(job["active"])
            self.assertEqual(self.store.pending_profile_ids(), ["1"])
        finally:
            os.remove(video_path)


class UploadPipelineResumeTests(unittest.TestCase):
    def test_worker_waits_for_telegram_reply_before_resolving_caption(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = {
                "id": "1",
                "name": "Test",
                "default_caption": "Caption mặc định",
                "caption_options": {
                    "telegram_use_custom_caption": True,
                    "telegram_pin_caption_message": True,
                },
                "douyin": {"gemlogin_profile_id": "1"},
                "tiktok": {"enabled": True},
                "facebook": {"enabled": False},
                "youtube": {"enabled": False},
            }
            source = {
                "source_key": "source-a",
                "platform": "douyin",
                "target_sec_uid": "source-user",
                "target_display_name": "Nguồn A",
            }
            video = make_video("telegram-caption")
            worker = ProfileWorker(profile)
            worker.job_store = VideoJobStore(os.path.join(temp_dir, "jobs.json"))
            worker.register_video(video, source)

            with patch(
                "application.workflows.profile_worker.create_caption_request",
                return_value="caption-request-1",
            ) as create_request, patch(
                "application.workflows.profile_worker.wait_for_caption",
                return_value="Caption từ Telegram",
            ) as wait_for_reply:
                profile_for_upload, _priority = worker.resolve_caption(
                    video,
                    source,
                    "douyin",
                )

            create_request.assert_called_once()
            self.assertTrue(create_request.call_args.kwargs["pin_message"])
            wait_for_reply.assert_called_once_with(
                "caption-request-1",
                cancel_event=None,
            )
            self.assertEqual(
                profile_for_upload["_runtime_caption"],
                "Caption từ Telegram",
            )
            job = worker.job_store.get_job("1", video.aweme_id)
            self.assertTrue(job["caption_resolved"])
            self.assertEqual(job["caption"], "Caption từ Telegram")
            self.assertEqual(job["caption_request_id"], "")

    def test_worker_resumes_existing_telegram_caption_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = {
                "id": "1",
                "name": "Test",
                "caption_options": {"telegram_use_custom_caption": True},
                "douyin": {"gemlogin_profile_id": "1"},
                "tiktok": {"enabled": True},
                "facebook": {"enabled": False},
                "youtube": {"enabled": False},
            }
            source = {
                "source_key": "source-a",
                "platform": "douyin",
                "target_sec_uid": "source-user",
            }
            video = make_video("resume-caption")
            worker = ProfileWorker(profile)
            worker.job_store = VideoJobStore(os.path.join(temp_dir, "jobs.json"))
            worker.register_video(video, source)
            worker.job_store.set_caption_request(
                "1",
                video.aweme_id,
                "caption-request-existing",
            )

            with patch(
                "application.workflows.profile_worker.create_caption_request",
            ) as create_request, patch(
                "application.workflows.profile_worker.wait_for_caption",
                return_value="Caption sau khi mở lại",
            ) as wait_for_reply:
                profile_for_upload, _priority = worker.resolve_caption(
                    video,
                    source,
                    "douyin",
                )

            create_request.assert_not_called()
            wait_for_reply.assert_called_once_with(
                "caption-request-existing",
                cancel_event=None,
            )
            self.assertEqual(
                profile_for_upload["_runtime_caption"],
                "Caption sau khi mở lại",
            )

    def test_pipeline_never_uploads_back_to_the_source_platform(self):
        pipeline = UploadPipeline()
        pipeline.tiktok_uploader = FakeUploader(result=True)
        pipeline.facebook_uploader = FakeUploader(result=True)
        profile = {
            "tiktok": {"enabled": True},
            "facebook": {"enabled": True},
            "youtube": {"enabled": False},
        }

        results = pipeline.run(
            "video.mp4",
            make_video(),
            profile,
            source_platform="tiktok",
        )

        self.assertEqual(enabled_platform_names(profile, "tiktok"), ["facebook"])
        self.assertEqual(pipeline.tiktok_uploader.calls, 0)
        self.assertEqual(pipeline.facebook_uploader.calls, 1)
        self.assertEqual(results, {"facebook": True})
        self.assertTrue(all_enabled_uploads_succeeded(results, profile, "tiktok"))

    def test_copyright_skip_is_terminal_and_not_an_upload_failure(self):
        pipeline = UploadPipeline()
        pipeline.youtube_uploader = FakeUploader(
            UploadSkipped("YouTube phát hiện nội dung bản quyền.")
        )
        statuses = []
        profile = {
            "tiktok": {"enabled": False},
            "facebook": {"enabled": False},
            "youtube": {"enabled": True},
        }

        results = pipeline.run(
            "video.mp4",
            make_video(),
            profile,
            on_platform_status=lambda platform, status, error: statuses.append(
                (platform, status, error)
            ),
        )

        self.assertEqual(results, {"youtube": "skipped"})
        self.assertTrue(all_enabled_uploads_succeeded(results, profile))
        self.assertEqual(statuses[0][:2], ("youtube", "uploading"))
        self.assertEqual(statuses[1][0:2], ("youtube", "skipped"))

    def test_pipeline_skips_successful_platform_and_retries_failed_platform(self):
        pipeline = UploadPipeline()
        pipeline.tiktok_uploader = FakeUploader(result=True)
        pipeline.facebook_uploader = FakeUploader(result=True)
        pipeline.youtube_uploader = FakeUploader(result=True)
        statuses = []
        profile = {
            "tiktok": {"enabled": True},
            "facebook": {"enabled": True},
            "youtube": {"enabled": False},
        }

        results = pipeline.run(
            "video.mp4",
            make_video(),
            profile,
            successful_platforms={"tiktok"},
            on_platform_status=lambda platform, status, error: statuses.append(
                (platform, status)
            ),
        )

        self.assertEqual(pipeline.tiktok_uploader.calls, 0)
        self.assertEqual(pipeline.facebook_uploader.calls, 1)
        self.assertEqual(pipeline.youtube_uploader.calls, 0)
        self.assertEqual(results, {"tiktok": True, "facebook": True})
        self.assertTrue(all_enabled_uploads_succeeded(results, profile))
        self.assertEqual(statuses, [("facebook", "uploading"), ("facebook", "success")])

    def test_worker_keeps_file_then_manual_retry_runs_only_failed_platform(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, "video.mp4")
            with open(video_path, "wb") as file_handle:
                file_handle.write(b"video")

            profile = {
                "id": "1",
                "name": "Test",
                "douyin": {"gemlogin_profile_id": "1"},
                "tiktok": {"enabled": True},
                "facebook": {"enabled": True},
                "youtube": {"enabled": False},
            }
            source = {
                "source_key": "source-a",
                "target_sec_uid": "source-user",
                "target_display_name": "Nguồn A",
            }
            video = make_video("456")
            monitor = FakeMonitor()
            worker = ProfileWorker(profile)
            worker.job_store = VideoJobStore(os.path.join(temp_dir, "jobs.json"))
            worker.pipeline.tiktok_uploader = FakeUploader(True)
            worker.pipeline.facebook_uploader = FakeUploader([False, True])
            worker.pipeline.youtube_uploader = FakeUploader(True)

            worker.register_video(video, source)
            worker.job_store.set_caption("1", video.aweme_id, "Caption #tag")
            worker.job_store.set_download_path("1", video.aweme_id, video_path)

            media_info = {
                "width": 1080,
                "height": 1920,
                "duration_seconds": 12.0,
                "has_audio": True,
                "file_size": os.path.getsize(video_path),
            }
            with patch(
                "application.workflows.profile_worker.send_error_notification"
            ), patch(
                "application.workflows.profile_worker.send_video_upload_summary_notification"
            ), patch(
                "application.workflows.profile_worker.validate_video_file",
                return_value=media_info,
            ):
                first = worker.process_video(video, source, monitor)
                worker.job_store.retry_job("1", video.aweme_id)
                second = worker.process_video(video, source, monitor)

            self.assertEqual(first["status"], "failed_upload")
            self.assertEqual(second["status"], "completed")
            self.assertEqual(worker.pipeline.tiktok_uploader.calls, 1)
            self.assertEqual(worker.pipeline.facebook_uploader.calls, 2)
            self.assertEqual(monitor.processed, ["456"])
            self.assertFalse(os.path.exists(video_path))
            job = worker.job_store.get_job("1", video.aweme_id)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["platforms"]["tiktok"]["status"], "success")
            self.assertEqual(job["platforms"]["facebook"]["status"], "success")
            self.assertEqual(job["media_info"]["duration_seconds"], 12.0)


if __name__ == "__main__":
    unittest.main()
