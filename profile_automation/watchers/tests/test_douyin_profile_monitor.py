import tempfile
import time
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from profile_automation.watchers.douyin_console import _analyze_aweme_payload
from profile_automation.watchers.douyin_profile_monitor import (
    DouyinProfileMonitor,
    _decode_douyin_response,
    _loggable_douyin_url,
)
from profile_automation.watchers.douyin_video import (
    DouyinVideo,
    _is_photo_aweme,
    _parse_aweme,
)


def _aweme(
    aweme_id: str,
    *,
    aweme_type: int = 0,
    media_type: int = 4,
    duration: int = 7_700,
) -> dict:
    return {
        "aweme_id": aweme_id,
        "aweme_type": aweme_type,
        "media_type": media_type,
        "duration": duration,
        "desc": "sample",
        "create_time": 1_700_000_000,
        "author": {"uid": "author", "nickname": "Author"},
        "statistics": {"digg_count": 10, "play_count": 20},
        "status": {"is_delete": 0, "private_status": 0},
        "music": {"duration": 180},
        "video": {
            "duration": duration,
            "play_addr": {
                "url_list": ["https://example.test/video.mp4"],
            },
        },
    }


class DouyinProfileMonitorTests(unittest.TestCase):
    class _FakePage:
        def __init__(self, responses):
            self.responses = responses
            self.listeners = []

        def on(self, event_name, listener):
            if event_name == "response":
                self.listeners.append(listener)

        def remove_listener(self, event_name, listener):
            if event_name == "response":
                self.listeners.remove(listener)

        def goto(self, *_args, **_kwargs):
            for response in self.responses:
                for listener in list(self.listeners):
                    listener(response)

        def reload(self, *_args, **_kwargs):
            self.goto()

        def evaluate(self, _script):
            self.goto()

        @staticmethod
        def wait_for_timeout(timeout_ms):
            time.sleep(timeout_ms / 1000)

    @staticmethod
    def _response(status: int, body: str, url_suffix: str = ""):
        response = Mock(
            status=status,
            headers={"content-type": "application/json"},
            url=(
                "https://www.douyin.com/aweme/v1/web/aweme/post/"
                f"?sec_user_id=source{url_suffix}"
            ),
        )
        response.request.method = "GET"
        response.request.resource_type = "xhr"
        response.text.return_value = body
        return response

    def test_type_68_photo_post_is_skipped_before_music_duration_fallback(self):
        item = _aweme(
            "photo-68",
            aweme_type=68,
            media_type=2,
            duration=0,
        )
        item["video"]["play_addr"] = {
            "uri": "https://example.test/background.mp3",
            "url_list": ["https://example.test/background.mp3"],
        }

        self.assertTrue(_is_photo_aweme(item))
        self.assertIsNone(_parse_aweme(item))

    def test_media_type_2_zero_duration_photo_is_skipped_without_aweme_type(self):
        item = _aweme(
            "photo-media",
            aweme_type=0,
            media_type=2,
            duration=0,
        )

        self.assertTrue(_is_photo_aweme(item))
        self.assertIsNone(_parse_aweme(item))

    def test_legacy_image_post_info_is_still_skipped(self):
        item = _aweme("photo-legacy")
        item["image_post_info"] = {"images": [{"url_list": ["https://image"]}]}

        self.assertTrue(_is_photo_aweme(item))
        self.assertIsNone(_parse_aweme(item))

    def test_regular_video_is_preserved(self):
        item = _aweme("video")

        self.assertFalse(_is_photo_aweme(item))
        video = _parse_aweme(item)

        self.assertIsNotNone(video)
        self.assertEqual(video.aweme_id, "video")
        self.assertEqual(video.duration_ms, 7_700)

    def test_analysis_counts_new_photo_shapes_as_image_posts(self):
        data = {
            "status_code": 0,
            "aweme_list": [
                _aweme("video"),
                _aweme("photo-68", aweme_type=68, media_type=2, duration=0),
                _aweme("photo-media", aweme_type=0, media_type=2, duration=0),
            ],
        }

        analysis = _analyze_aweme_payload(data)

        self.assertEqual(analysis["valid_video_count"], 1)
        self.assertEqual(analysis["image_post_count"], 2)
        self.assertEqual(analysis["invalid_duration_count"], 0)

    def test_monitor_keeps_browser_profile_config(self):
        profile = {
            "id": "1",
            "browser": {
                "provider": "local_chromium",
                "user_data_dir": r"C:\profiles\profiles\1",
            },
        }
        with tempfile.TemporaryDirectory() as state_dir:
            monitor = DouyinProfileMonitor(
                profile_id="1",
                sec_uid="source",
                gemlogin_profile_id="1",
                state_dir=state_dir,
                source_key="source-key",
                profile_config=profile,
            )

        self.assertIs(monitor.profile_config, profile)

    def test_empty_douyin_response_reports_http_details(self):
        response = Mock(
            status=200,
            headers={
                "content-type": "application/json",
                "content-length": "0",
            },
        )
        response.text.return_value = ""

        with self.assertRaisesRegex(
            ValueError,
            r"dữ liệu rỗng \(HTTP 200, độ dài=0\)",
        ):
            _decode_douyin_response(response)

    def test_valid_douyin_response_is_decoded(self):
        response = Mock(
            status=200,
            headers={"content-type": "application/json"},
        )
        response.text.return_value = '{"status_code":0,"aweme_list":[]}'

        data = _decode_douyin_response(response)

        self.assertEqual(data["status_code"], 0)
        self.assertEqual(data["aweme_list"], [])

    def test_loggable_url_redacts_signing_tokens(self):
        url = (
            "https://www-hj.douyin.com/aweme/v1/web/aweme/post/"
            "?sec_user_id=source&msToken=secret&a_bogus=signed"
            "&x-secsdk-web-signature=signature&count=18"
        )

        logged = _loggable_douyin_url(url)

        self.assertIn("sec_user_id=source", logged)
        self.assertIn("count=18", logged)
        self.assertNotIn("secret", logged)
        self.assertNotIn("signed", logged)
        self.assertNotIn("signature=signature", logged)
        self.assertEqual(logged.count("<redacted>"), 3)

    def test_capture_page_skips_403_and_uses_later_valid_200(self):
        blocked = self._response(
            403,
            "Blocked by ArgusSecurityPlugin Sign Invalid",
            "&attempt=1",
        )
        valid = self._response(
            200,
            '{"status_code":0,"aweme_list":[]}',
            "&attempt=2",
        )
        page = self._FakePage([blocked, valid])
        with tempfile.TemporaryDirectory() as state_dir:
            monitor = DouyinProfileMonitor(
                "1", "source", "browser", state_dir=state_dir
            )

            captured = monitor._capture_page(page, "https://profile", 1, 0.1)

        self.assertIsNotNone(captured)
        response, data = captured
        self.assertIs(response, valid)
        self.assertEqual(data["status_code"], 0)
        self.assertEqual(page.listeners, [])

    def test_capture_page_skips_200_without_aweme_list(self):
        incomplete = self._response(
            200,
            '{"status_code":0}',
            "&attempt=1",
        )
        valid = self._response(
            200,
            '{"status_code":0,"aweme_list":[]}',
            "&attempt=2",
        )
        page = self._FakePage([incomplete, valid])
        with tempfile.TemporaryDirectory() as state_dir:
            monitor = DouyinProfileMonitor(
                "1", "source", "browser", state_dir=state_dir
            )

            captured = monitor._capture_page(page, "https://profile", 1, 0.1)

        self.assertIsNotNone(captured)
        response, _data = captured
        self.assertIs(response, valid)

    def test_capture_page_distinguishes_only_403_from_no_response(self):
        blocked = self._response(
            403,
            "Blocked by ArgusSecurityPlugin Sign Invalid",
        )
        with tempfile.TemporaryDirectory() as state_dir:
            monitor = DouyinProfileMonitor(
                "1", "source", "browser", state_dir=state_dir
            )
            with self.assertLogs(level="WARNING") as captured_logs:
                result = monitor._capture_page(
                    self._FakePage([blocked]), "https://profile", 1, 0.01
                )

        self.assertIsNone(result)
        logs = "\n".join(captured_logs.output)
        self.assertIn("Douyin đã phản hồi 1 lần", logs)
        self.assertIn("HTTP=[403]", logs)
        self.assertNotIn("Không bắt được response Douyin", logs)

    def test_capture_page_reports_when_no_matching_response_exists(self):
        with tempfile.TemporaryDirectory() as state_dir:
            monitor = DouyinProfileMonitor(
                "1", "source", "browser", state_dir=state_dir
            )
            with self.assertLogs(level="WARNING") as captured_logs:
                result = monitor._capture_page(
                    self._FakePage([]), "https://profile", 1, 0.01
                )

        self.assertIsNone(result)
        logs = "\n".join(captured_logs.output)
        self.assertIn("Không nhận được dữ liệu video Douyin", logs)
        self.assertNotIn("Đã bắt được 1 response Douyin", logs)

    def test_fetch_retries_once_after_403_with_full_browser_runtime(self):
        valid_response = self._response(
            200,
            '{"status_code":0,"aweme_list":[]}',
        )
        page = Mock()
        page.is_closed.return_value = False
        browser = Mock(contexts=[Mock()])

        @contextmanager
        def connected(*_args, **kwargs):
            connection_kwargs.append(kwargs)
            yield browser

        with tempfile.TemporaryDirectory() as state_dir:
            monitor = DouyinProfileMonitor(
                "1", "source", "browser", state_dir=state_dir
            )
            capture_calls = []
            connection_kwargs = []

            def capture(*_args, **kwargs):
                capture_calls.append(kwargs)
                if len(capture_calls) < 2:
                    monitor._last_capture_http_statuses = [403]
                    return None
                return valid_response, {"status_code": 0, "aweme_list": []}

            with (
                patch(
                    "profile_automation.watchers.douyin_profile_monitor.connected_gemlogin_profile",
                    connected,
                ),
                patch(
                    "profile_automation.watchers.douyin_profile_monitor.create_background_page",
                    return_value=page,
                ),
                patch.object(monitor, "_capture_page", side_effect=capture),
            ):
                videos = monitor.fetch_latest_videos(pages_to_fetch=1)

        self.assertEqual(videos, [])
        self.assertEqual(len(capture_calls), 2)
        self.assertFalse(capture_calls[0]["reload_page"])
        self.assertTrue(capture_calls[1]["reload_page"])
        self.assertFalse(connection_kwargs[0]["resource_saving"])

    def test_get_new_videos_logs_an_unambiguous_scan_result(self):
        def video(aweme_id: str, likes: int = 10, duration_ms: int = 7_000):
            return DouyinVideo(
                aweme_id=aweme_id,
                share_url=f"https://www.douyin.com/video/{aweme_id}",
                desc="sample",
                create_time=1_700_000_000,
                duration_ms=duration_ms,
                like_count=likes,
                play_count=20,
                author_uid="author",
                author_nickname="Author",
            )

        with tempfile.TemporaryDirectory() as state_dir:
            monitor = DouyinProfileMonitor(
                "3",
                "source",
                "browser",
                min_likes=10,
                max_duration_sec=10,
                state_dir=state_dir,
            )
            monitor.state.replace_seen(["seen"])
            monitor.fetch_latest_videos = Mock(
                return_value=[
                    video("seen"),
                    video("low-likes", likes=9),
                    video("too-long", duration_ms=11_000),
                    video("new"),
                ]
            )

            with self.assertLogs(level="INFO") as captured_logs:
                new_videos = monitor.get_new_videos()

        self.assertEqual([item.aweme_id for item in new_videos], ["new"])
        result_log = "\n".join(captured_logs.output)
        self.assertIn("KẾT QUẢ QUÉT DOUYIN", result_log)
        self.assertIn("kiểm tra 4 video | MỚI 1 | đã xử lý 1", result_log)
        self.assertIn("loại do thiếu lượt thích 1 | loại do quá dài 1", result_log)


if __name__ == "__main__":
    unittest.main()
