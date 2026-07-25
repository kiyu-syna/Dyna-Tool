import os
import inspect
import json
import tempfile
import unittest
from contextlib import contextmanager
from urllib.parse import quote
from unittest.mock import patch

from services.publishing.video_validation_service import InvalidVideoFileError
from profile_automation.pipeline.downloads.douyin import (
    _candidate_video_page_urls,
    _download_douyin_candidates,
    _download_douyin_url,
    _download_result_from_payload,
    _extract_douyin_ssr_result,
    _install_douyin_response_capture,
    _try_douyin_extension_result,
    _wait_for_douyin_download_result,
    _download_douyin_video_direct_once,
    check_douyin_direct_download,
    download_douyin_video_direct,
)
from profile_automation.watchers.douyin_profile_monitor import (
    MAX_AWEME_IDS_PER_SCAN,
    DouyinVideo,
    _analyze_aweme_payload,
    _parse_aweme,
    extract_douyin_download_urls,
)


class FakeHandle:
    def __init__(self, value):
        self.value = value
        self.disposed = False

    def json_value(self):
        return self.value

    def dispose(self):
        self.disposed = True


class FakeBridgePage:
    url = "https://www.douyin.com/video/123"

    def __init__(self):
        self.calls = []

    def wait_for_function(self, expression, **kwargs):
        self.calls.append((expression, kwargs))
        if "getByAwemeId(expectedId)" in expression:
            return FakeHandle(
                {
                    "status": "success",
                    "aweme_id": "123",
                    "download_url": "https://video.example/123.mp4",
                }
            )
        return FakeHandle(True)

    def evaluate(self, expression):
        if "Boolean(" in expression and "__AixDouyinAutomation" in expression:
            return True
        return None


class FakeResponseCapturePage:
    def __init__(self):
        self.response_callback = None

    def on(self, event_name, callback):
        if event_name == "response":
            self.response_callback = callback


class FakeJsonResponse:
    url = "https://www.douyin.com/v1/web/feed/"
    headers = {"content-type": "application/json; charset=utf-8"}

    def json(self):
        return {
            "aweme_list": [
                {
                    "aweme_id": "123",
                    "video": {
                        "play_addr": {"url_list": ["https://video.example/feed-response.mp4"]}
                    },
                }
            ]
        }


class FakeSsrPage:
    def evaluate(self, expression):
        payload = {
            "aweme_detail": {
                "aweme_id": "123",
                "video": {
                    "play_addr": {"url_list": ["https://video.example/ssr.mp4"]}
                },
            }
        }
        return {
            "render_data": quote(json.dumps(payload)),
            "initial_state": "",
            "init_props": "",
        }


class FakePreflightPage:
    url = "https://www.douyin.com/"

    def __init__(self):
        self.closed = False

    def goto(self, *args, **kwargs):
        return None

    def evaluate(self, expression):
        return None

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True


class FakeContext:
    def cookies(self, urls):
        return [{"name": "sessionid", "value": "cookie-value"}]


class FakeResponse:
    headers = {"content-type": "video/mp4"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        return [b"video-data" * 200]


class DouyinDirectDownloadTests(unittest.TestCase):
    def test_aweme_analysis_keeps_at_most_four_sample_ids(self):
        payload = {
            "aweme_list": [
                {"aweme_id": str(index), "duration": 1000}
                for index in range(1, 8)
            ]
        }

        analysis = _analyze_aweme_payload(payload)

        self.assertEqual(MAX_AWEME_IDS_PER_SCAN, 4)
        self.assertEqual(analysis["sample_ids"], ["1", "2", "3", "4"])

    def test_active_download_path_never_focuses_or_clicks_browser_ui(self):
        source = "\n".join(
            (
                inspect.getsource(download_douyin_video_direct),
                inspect.getsource(_download_douyin_video_direct_once),
            )
        )

        self.assertNotIn("pyautogui", source)
        self.assertNotIn("bring_to_front", source)
        self.assertNotIn("bring_window_to_top", source)
        self.assertNotIn("_debug_click", source)

    def test_extension_fallback_result_is_requested_by_aweme_id(self):
        page = FakeBridgePage()
        result = _try_douyin_extension_result(page, "123", timeout_seconds=1)

        self.assertEqual(result["aweme_id"], "123")
        self.assertEqual(page.calls[0][1]["arg"], "123")

    def test_captured_response_is_used_without_checking_extension(self):
        captured = {
            "status": "success",
            "aweme_id": "123",
            "download_url": "https://video.example/direct.mp4",
        }
        page = FakeBridgePage()

        result = _wait_for_douyin_download_result(
            page,
            "123",
            timeout_seconds=1,
            captured_result=captured,
        )

        self.assertEqual(result["download_url"], "https://video.example/direct.mp4")
        self.assertEqual(page.calls, [])

    @patch(
        "profile_automation.pipeline.downloads.douyin._try_douyin_extension_result",
        return_value={
            "status": "success",
            "aweme_id": "123",
            "download_url": "https://video.example/extension.mp4",
        },
    )
    @patch(
        "profile_automation.pipeline.downloads.douyin._wait_for_douyin_direct_result",
        side_effect=RuntimeError("direct unavailable"),
    )
    def test_extension_is_only_used_after_direct_sources_fail(self, direct_wait, extension_wait):
        result = _wait_for_douyin_download_result(
            FakeBridgePage(),
            "123",
            timeout_seconds=2,
        )

        self.assertEqual(result["download_url"], "https://video.example/extension.mp4")
        direct_wait.assert_called_once()
        extension_wait.assert_called_once()

    def test_encoded_render_data_is_read_without_extension(self):
        result = _extract_douyin_ssr_result(FakeSsrPage(), "123")

        self.assertEqual(result["aweme_id"], "123")
        self.assertEqual(result["download_url"], "https://video.example/ssr.mp4")

    def test_feed_response_url_is_captured_without_aweme_word_requirement(self):
        page = FakeResponseCapturePage()
        captured = _install_douyin_response_capture(page, "123")

        page.response_callback(FakeJsonResponse())

        self.assertEqual(captured["download_url"], "https://video.example/feed-response.mp4")

    @patch("profile_automation.pipeline.downloads.douyin.create_background_page")
    @patch("profile_automation.pipeline.downloads.douyin.connected_gemlogin_profile")
    def test_preflight_succeeds_when_extension_is_not_installed(self, connect_profile, create_page):
        class FakeBrowser:
            contexts = [object()]

        @contextmanager
        def fake_connection():
            yield FakeBrowser()

        connect_profile.return_value = fake_connection()
        create_page.return_value = FakePreflightPage()

        result = check_douyin_direct_download("2")

        self.assertTrue(result["ok"])
        self.assertFalse(result["extension_ready"])
        self.assertEqual(result["mode"], "direct_only")

    def test_playwright_response_payload_is_a_bridge_fallback(self):
        result = _download_result_from_payload(
            {
                "aweme_detail": {
                    "aweme_id": "123",
                    "video": {
                        "bit_rate": [
                            {
                                "bit_rate": 1000,
                                "play_addr": {"url_list": ["https://video.example/low.mp4"]},
                            },
                            {
                                "bit_rate": 3000,
                                "play_addr": {"url_list": ["https://video.example/high.mp4"]},
                            },
                        ]
                    },
                }
            },
            "123",
            "playwright_response:test",
        )

        self.assertEqual(result["aweme_id"], "123")
        self.assertEqual(result["download_url"], "https://video.example/high.mp4")

    def test_direct_url_prefers_highest_menu_resolution_over_bitrate(self):
        result = _download_result_from_payload(
            {
                "aweme_detail": {
                    "aweme_id": "123",
                    "video": {
                        "bit_rate": [
                            {
                                "width": 720,
                                "height": 1280,
                                "bit_rate": 5000,
                                "data_size": 8_000_000,
                                "play_addr": {"url_list": ["https://video.example/720p.mp4"]},
                            },
                            {
                                "width": 1080,
                                "height": 1920,
                                "bit_rate": 3000,
                                "data_size": 7_000_000,
                                "play_addr": {"url_list": ["https://video.example/1080p.mp4"]},
                            },
                        ]
                    },
                }
            },
            "123",
            "playwright_response:test",
        )

        self.assertEqual(result["download_url"], "https://video.example/1080p.mp4")

    def test_direct_url_prefers_larger_file_at_same_resolution(self):
        result = _download_result_from_payload(
            {
                "aweme_detail": {
                    "aweme_id": "123",
                    "video": {
                        "bit_rate": [
                            {
                                "width": 1080,
                                "height": 1920,
                                "bit_rate": 5000,
                                "data_size": 6_000_000,
                                "play_addr": {"url_list": ["https://video.example/smaller.mp4"]},
                            },
                            {
                                "width": 1080,
                                "height": 1920,
                                "bit_rate": 3000,
                                "data_size": 9_000_000,
                                "play_addr": {"url_list": ["https://video.example/larger.mp4"]},
                            },
                        ]
                    },
                }
            },
            "123",
            "playwright_response:test",
        )

        self.assertEqual(result["download_url"], "https://video.example/larger.mp4")

    def test_direct_url_candidates_preserve_quality_order_and_mirrors(self):
        candidates = extract_douyin_download_urls(
            {
                "video": {
                    "bit_rate": [
                        {
                            "width": 720,
                            "height": 1280,
                            "play_addr": {
                                "url_list": ["https://video.example/720p.mp4"]
                            },
                        },
                        {
                            "width": 1080,
                            "height": 1920,
                            "play_addr": {
                                "url_list": [
                                    "https://video.example/1080p-a.mp4",
                                    "https://video.example/1080p-b.mp4",
                                ]
                            },
                        },
                    ],
                    "play_addr": {
                        "url_list": ["https://video.example/fallback.mp4"]
                    },
                }
            }
        )

        self.assertEqual(
            candidates,
            [
                "https://video.example/1080p-a.mp4",
                "https://video.example/1080p-b.mp4",
                "https://video.example/720p.mp4",
                "https://video.example/fallback.mp4",
            ],
        )

    def test_feed_parser_persists_direct_url_with_video(self):
        video = _parse_aweme(
            {
                "aweme_id": "123",
                "duration": 12000,
                "desc": "Test",
                "create_time": 100,
                "statistics": {},
                "author": {},
                "video": {
                    "play_addr": {"url_list": ["https://video.example/from-feed.mp4"]}
                },
            },
            "sec-user",
        )

        self.assertIsNotNone(video)
        self.assertEqual(video.download_url, "https://video.example/from-feed.mp4")

    def test_downloader_alternates_direct_and_modal_page_urls(self):
        video = DouyinVideo(
            aweme_id="123",
            share_url="https://www.douyin.com/user/sec-user?modal_id=123",
            desc="Test",
            create_time=100,
            duration_ms=12000,
            like_count=1,
            play_count=1,
            author_uid="author",
            author_nickname="Tester",
        )

        self.assertEqual(
            _candidate_video_page_urls(video),
            [
                "https://www.douyin.com/video/123",
                "https://www.douyin.com/user/sec-user?modal_id=123",
            ],
        )

    @patch("profile_automation.pipeline.downloads.douyin.validate_video_file")
    @patch("requests.get", return_value=FakeResponse())
    def test_direct_download_uses_profile_cookie_and_writes_atomically(
        self,
        get_request,
        validate_file,
    ):
        validate_file.return_value = {
            "width": 1080,
            "height": 1920,
            "duration_seconds": 12.0,
        }
        page = FakeBridgePage()
        result = {
            "download_url": "https://video.example/123.mp4",
            "referer": page.url,
            "user_agent": "GemLogin UA",
            "headers": {},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = os.path.join(temp_dir, "video.mp4")
            downloaded_path = _download_douyin_url(FakeContext(), page, result, save_path)

            self.assertEqual(downloaded_path, save_path)
            self.assertTrue(os.path.isfile(save_path))
            self.assertFalse(os.path.exists(f"{save_path}.part"))

            validated_path = validate_file.call_args.args[0]
            self.assertEqual(validated_path, f"{save_path}.part")

        request_kwargs = get_request.call_args.kwargs
        self.assertEqual(request_kwargs["cookies"]["sessionid"], "cookie-value")
        self.assertEqual(request_kwargs["headers"]["User-Agent"], "GemLogin UA")

    @patch("profile_automation.pipeline.downloads.douyin._download_douyin_url")
    def test_invalid_first_media_candidate_tries_the_next_url(self, download_url):
        download_url.side_effect = [
            InvalidVideoFileError("File không có luồng hình ảnh."),
            "C:/videos/valid.mp4",
        ]
        result = {
            "download_url": "https://video.example/audio-only.mp4",
            "download_urls": [
                "https://video.example/audio-only.mp4",
                "https://video.example/valid.mp4",
            ],
        }

        downloaded = _download_douyin_candidates(
            FakeContext(),
            FakeBridgePage(),
            result,
            "C:/videos/valid.mp4",
        )

        self.assertEqual(downloaded, "C:/videos/valid.mp4")
        self.assertEqual(download_url.call_count, 2)
        self.assertEqual(
            download_url.call_args_list[1].args[2]["download_url"],
            "https://video.example/valid.mp4",
        )


if __name__ == "__main__":
    unittest.main()
