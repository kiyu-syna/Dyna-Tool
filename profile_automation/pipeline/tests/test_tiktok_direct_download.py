import unittest
from contextlib import contextmanager
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from profile_automation.pipeline.downloads.captions import get_tiktok_tracking_download_path
from profile_automation.pipeline.downloads.tiktok import (
    _download_tiktok_video_direct_once,
)
from profile_automation.pipeline.profile_worker import ProfileWorker
from profile_automation.pipeline.video_job_store import VideoJobStore
from profile_automation.tracking_sources import get_tracking_sources
from profile_automation.watchers.tiktok_profile_monitor import TikTokVideo
from profile_automation.watchers.tiktok_profile_monitor import TikTokProfileMonitor


class FakeDownloadPage:
    def __init__(self):
        self.url = "about:blank"
        self.goto_calls = []
        self.closed = False

    def goto(self, url, **kwargs):
        self.url = url
        self.goto_calls.append((url, kwargs))

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True


def make_video() -> TikTokVideo:
    video_id = "7664560848664382740"
    direct_url = f"https://www.tiktok.com/aweme/v1/play/?item_id={video_id}"
    return TikTokVideo(
        aweme_id=video_id,
        share_url=f"https://www.tiktok.com/@jettvn/video/{video_id}",
        desc="",
        create_time=0,
        duration_ms=1000,
        like_count=0,
        play_count=0,
        author_uid="author",
        author_nickname="jettvn",
        download_url=direct_url,
        download_urls=[direct_url, f"https://backup.example/{video_id}.mp4"],
    )


class TikTokDirectDownloadTests(unittest.TestCase):
    def test_profile_worker_dispatches_tiktok_source_to_tiktok_monitor(self):
        profile = {
            "id": "1",
            "douyin": {"gemlogin_profile_id": "browser-1"},
            "tracking_sources": [
                {
                    "platform": "tiktok",
                    "profile_url": "https://www.tiktok.com/@jettvn",
                    "enabled": True,
                }
            ],
        }

        worker = ProfileWorker(profile)
        monitor = worker._create_monitor(get_tracking_sources(profile)[0])

        self.assertIsInstance(monitor, TikTokProfileMonitor)
        self.assertEqual(monitor.unique_id, "jettvn")

    def test_pending_tiktok_job_is_rebuilt_as_tiktok_video(self):
        video = make_video()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = VideoJobStore(str(Path(temp_dir) / "jobs.json"))
            store.ensure_job(
                "1",
                video,
                {"tiktok": {"enabled": True}},
                source_key="tiktok-source",
                source_label="Jett",
                source_platform="tiktok",
            )

            pending = store.list_pending_videos(
                "1",
                source_key="tiktok-source",
                source_platform="tiktok",
            )

        self.assertEqual(len(pending), 1)
        self.assertIsInstance(pending[0], TikTokVideo)
        self.assertEqual(pending[0].aweme_id, video.aweme_id)

    def test_process_video_dispatches_to_tiktok_direct_downloader(self):
        video = make_video()
        profile = {
            "id": "1",
            "name": "TikTok source",
            "douyin": {"gemlogin_profile_id": "browser-1"},
            "tracking_sources": [
                {"platform": "tiktok", "unique_id": "jettvn", "enabled": True}
            ],
            "tiktok": {"enabled": True, "use_original_desc": True},
            "facebook": {"enabled": True},
            "youtube": {"enabled": False},
        }
        source = get_tracking_sources(profile)[0]
        monitor = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "video.mp4"
            video_path.write_bytes(b"video")
            worker = ProfileWorker(profile)
            worker.job_store = VideoJobStore(str(Path(temp_dir) / "jobs.json"))
            worker.pipeline = Mock()
            worker.pipeline.run.return_value = {"facebook": True}

            with (
                patch(
                    "profile_automation.pipeline.profile_worker.download_tiktok_video_direct",
                    return_value=str(video_path),
                ) as tiktok_download,
                patch(
                    "profile_automation.pipeline.profile_worker.download_douyin_video_direct"
                ) as douyin_download,
                patch(
                    "profile_automation.pipeline.profile_worker.validate_video_file",
                    return_value={
                        "width": 576,
                        "height": 1024,
                        "duration_seconds": 10.0,
                        "has_audio": True,
                        "file_size": 5,
                    },
                ),
                patch(
                    "profile_automation.pipeline.profile_worker.send_video_upload_summary_notification"
                ),
            ):
                result = worker.process_video(video, source, monitor)

        self.assertEqual(result["status"], "completed")
        tiktok_download.assert_called_once()
        douyin_download.assert_not_called()
        monitor.mark_processed.assert_called_once_with(video)

    def test_tiktok_download_path_is_separate_from_douyin(self):
        path = get_tiktok_tracking_download_path("2", "123")

        self.assertIn("Tracking TikTok", path)
        self.assertTrue(path.endswith("[Profile 2]_123.mp4"))

    def test_direct_downloader_uses_item_list_urls_without_opening_video_page(self):
        video = make_video()
        page = FakeDownloadPage()
        context = object()
        browser = type("FakeBrowser", (), {"contexts": [context]})()

        @contextmanager
        def fake_connected_browser(*args, **kwargs):
            yield browser

        with (
            patch(
                "profile_automation.pipeline.downloads.tiktok.connected_gemlogin_profile",
                fake_connected_browser,
            ),
            patch(
                "profile_automation.pipeline.downloads.tiktok.create_background_page",
                return_value=page,
            ),
            patch(
                "profile_automation.pipeline.downloads.tiktok.attach_response_trace",
                return_value={},
            ),
            patch(
                "profile_automation.pipeline.downloads.tiktok._download_douyin_candidates",
                return_value="C:\\Videos\\tiktok-direct.mp4",
            ) as download_candidates,
        ):
            downloaded = _download_tiktok_video_direct_once(
                video,
                profile_id="1",
                gemlogin_profile_id="browser-1",
            )

        self.assertEqual(downloaded, "C:\\Videos\\tiktok-direct.mp4")
        self.assertEqual(page.goto_calls, [])
        passed_result = download_candidates.call_args.args[2]
        self.assertEqual(passed_result["source"], "tiktok_profile_item_list")
        self.assertEqual(passed_result["download_urls"], video.download_urls)
        self.assertEqual(passed_result["referer"], video.share_url)
        self.assertEqual(download_candidates.call_args.kwargs["platform_label"], "TikTok")

if __name__ == "__main__":
    unittest.main()
