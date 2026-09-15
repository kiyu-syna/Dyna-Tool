import unittest
import threading
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.integrations import telegram_service


class TelegramRelayTests(unittest.TestCase):
    def setUp(self):
        telegram_service._ERROR_NOTIFICATION_TIMES.clear()

    @patch.object(telegram_service.auth_service, "get_current_user")
    def test_headers_use_authenticated_dyna_token(self, get_current_user):
        get_current_user.return_value = {"token": "access-token"}
        self.assertEqual(
            telegram_service._headers(),
            {"Authorization": "Bearer access-token"},
        )

    @patch.object(telegram_service, "_request")
    def test_new_video_notification_relays_cancelable_job(self, request):
        video = SimpleNamespace(
            aweme_id="123456",
            share_url="https://www.douyin.com/video/123456",
        )
        telegram_service.send_new_video_notification(
            "2",
            "Valorant edit",
            "Nguồn A",
            video,
            ["tiktok", "youtube"],
        )

        method, path = request.call_args.args
        body = request.call_args.kwargs["body"]
        self.assertEqual((method, path), ("POST", "/api/telegram/notifications"))
        self.assertEqual(
            body["cancel_job"],
            {"profile_id": "2", "video_id": "123456"},
        )
        self.assertIn("Hồ sơ: 2 - Valorant edit", body["text"])
        self.assertIn("TikTok, YouTube Shorts", body["text"])

    @patch.object(telegram_service, "_request")
    def test_duplicate_cdp_error_is_suppressed_for_ten_minutes(self, request):
        telegram_service.send_error_notification(
            "connect_over_cdp: ECONNREFUSED 127.0.0.1:9222",
            profile_id="2",
            video_id="123",
        )
        telegram_service.send_error_notification(
            "connect_over_cdp: ECONNREFUSED 127.0.0.1:9444",
            profile_id="2",
            video_id="123",
        )
        self.assertEqual(request.call_count, 1)

    @patch.object(telegram_service, "_request")
    def test_upload_summary_relays_each_platform_outcome(self, request):
        video = SimpleNamespace(share_url="https://www.douyin.com/video/123456")
        telegram_service.send_video_upload_summary_notification(
            "2",
            "Valorant edit",
            "Nguồn A",
            video,
            ["tiktok", "facebook", "youtube"],
            {"tiktok": True, "facebook": False, "youtube": "skipped"},
            platform_states={
                "tiktok": {"status": "success"},
                "facebook": {"status": "failed", "last_error": "Không thấy nút Đăng"},
                "youtube": {"status": "skipped", "last_error": "Phát hiện bản quyền"},
            },
        )

        text = request.call_args.kwargs["body"]["text"]
        self.assertIn("TikTok: Thành công", text)
        self.assertIn("Facebook Reels: Không thành công - Không thấy nút Đăng", text)
        self.assertIn("YouTube Shorts: Không thành công - Phát hiện bản quyền", text)

    @patch.object(telegram_service, "_request")
    def test_diagnostic_is_relayed_without_local_file_upload(self, request):
        telegram_service.send_diagnostic_notification(
            {
                "profile_id": "2",
                "video_id": "123",
                "platform": "youtube",
                "error": "chooser timeout",
            }
        )

        text = request.call_args.kwargs["body"]["text"]
        self.assertIn("Hồ sơ: 2", text)
        self.assertIn("Nền tảng: youtube", text)
        self.assertIn("chooser timeout", text)

    @patch.object(telegram_service, "_request")
    @patch.object(telegram_service, "_request_file", return_value={"delivered": True})
    def test_diagnostic_sends_the_captured_image(self, request_file, request):
        with tempfile.TemporaryDirectory() as directory:
            screenshot = Path(directory) / "screenshot.png"
            screenshot.write_bytes(b"png")

            telegram_service.send_diagnostic_notification(
                {
                    "profile_id": "2",
                    "video_id": "123",
                    "platform": "tiktok",
                    "error": "confirmation timeout",
                    "screenshot_path": str(screenshot),
                }
            )

        request_file.assert_called_once()
        self.assertEqual(request_file.call_args.args[0], "/api/telegram/diagnostics")
        self.assertEqual(request_file.call_args.kwargs["image_path"], str(screenshot))
        request.assert_not_called()

    @patch.object(telegram_service, "_request")
    def test_caption_request_includes_pin_preference(self, request):
        request.return_value = {
            "available": True,
            "request_id": "caption-request-1",
            "pinned": True,
        }

        request_id = telegram_service.create_caption_request(
            profile_id="2",
            profile_name="Valorant edit",
            source_label="Nguồn A",
            video_id="123",
            description="Mô tả nguồn",
            default_caption="Caption mặc định",
            pin_message=True,
        )

        self.assertEqual(request_id, "caption-request-1")
        self.assertEqual(
            request.call_args.args,
            ("POST", "/api/telegram/captions"),
        )
        self.assertTrue(request.call_args.kwargs["body"]["pin_message"])

    @patch.object(telegram_service, "_request")
    def test_caption_wait_has_no_deadline_and_returns_selected_reply(self, request):
        request.side_effect = [
            None,
            {"status": "pending", "caption": ""},
            {"status": "selected", "caption": "Caption từ Telegram"},
        ]

        caption = telegram_service.wait_for_caption(
            "caption-request-1",
            poll_interval_seconds=0,
        )

        self.assertEqual(caption, "Caption từ Telegram")
        self.assertEqual(request.call_count, 3)

    @patch.object(telegram_service, "_request")
    def test_caption_wait_can_stop_with_profile(self, request):
        stop_event = threading.Event()
        stop_event.set()

        with self.assertRaises(InterruptedError):
            telegram_service.wait_for_caption(
                "caption-request-1",
                cancel_event=stop_event,
                poll_interval_seconds=0,
            )

        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
