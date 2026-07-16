import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services import telegram_service


class FakeTelegramBot:
    def __init__(self):
        self.last_message = None
        self.last_args = None
        self.messages = []
        self.message_count = 0
        self.photos = []
        self.documents = []

    def send_message(self, *args, **kwargs):
        self.last_args = args
        self.last_message = kwargs
        self.messages.append((args, kwargs))
        self.message_count += 1

    def send_photo(self, *args, **kwargs):
        self.photos.append((args, kwargs))

    def send_document(self, *args, **kwargs):
        self.documents.append((args, kwargs))


class TelegramErrorActionTests(unittest.TestCase):
    def setUp(self):
        telegram_service._ERROR_NOTIFICATION_TIMES.clear()

    @patch("services.telegram_service.is_telegram_quiet_hours_active", return_value=False)
    @patch("services.telegram_service._ensure_bot")
    def test_video_error_contains_retry_and_cancel_buttons(self, ensure_bot, _quiet_hours):
        bot = FakeTelegramBot()
        ensure_bot.return_value = bot

        telegram_service.send_error_notification(
            "Tải video thất bại",
            profile_id="1",
            video_id="123456",
        )

        keyboard = bot.last_message["reply_markup"].keyboard
        callbacks = [button.callback_data for row in keyboard for button in row]
        self.assertIn("job_retry|1|123456", callbacks)
        self.assertIn("job_cancel|1|123456", callbacks)

    @patch("services.telegram_service.is_telegram_quiet_hours_active", return_value=False)
    @patch("services.telegram_service._ensure_bot")
    def test_duplicate_cdp_error_is_suppressed_for_ten_minutes(
        self, ensure_bot, _quiet_hours
    ):
        bot = FakeTelegramBot()
        ensure_bot.return_value = bot

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

        self.assertEqual(bot.message_count, 1)

    @patch("services.telegram_service.is_telegram_quiet_hours_active", return_value=False)
    @patch("services.telegram_service._ensure_bot")
    def test_new_video_notification_lists_profile_source_and_platform_plan(
        self, ensure_bot, _quiet_hours
    ):
        bot = FakeTelegramBot()
        ensure_bot.return_value = bot
        video = SimpleNamespace(
            aweme_id="123456",
            share_url="https://www.douyin.com/video/123456",
            desc="Mô tả gốc",
            author_nickname="Tác giả A",
            duration_seconds=12.5,
            like_count=3210,
        )

        telegram_service.send_new_video_notification(
            "2",
            "Valorant edit",
            "Nguồn A",
            video,
            ["tiktok", "youtube"],
        )

        message = bot.last_args[1]
        self.assertIn("Profile: 2 - Valorant edit", message)
        self.assertIn("Nguồn: Nguồn A", message)
        self.assertIn("Sẽ đăng lên: TikTok, YouTube Shorts", message)
        self.assertIn("Lượt thích: 3,210", message)

    @patch("services.telegram_service.is_telegram_quiet_hours_active", return_value=False)
    @patch("services.telegram_service._ensure_bot")
    def test_upload_summary_reports_each_platform_outcome(
        self, ensure_bot, _quiet_hours
    ):
        bot = FakeTelegramBot()
        ensure_bot.return_value = bot
        video = SimpleNamespace(
            aweme_id="123456",
            share_url="https://www.douyin.com/video/123456",
        )

        telegram_service.send_video_upload_summary_notification(
            "2",
            "Valorant edit",
            "Nguồn A",
            video,
            ["tiktok", "facebook", "youtube"],
            {"tiktok": True, "facebook": False, "youtube": "skipped"},
            platform_states={
                "tiktok": {"status": "success", "last_error": ""},
                "facebook": {"status": "failed", "last_error": "Không tìm thấy nút Đăng"},
                "youtube": {"status": "skipped", "last_error": "Phát hiện bản quyền"},
            },
        )

        message = bot.last_args[1]
        self.assertIn("TikTok: Đăng thành công", message)
        self.assertIn("Facebook Reels: Thất bại - Không tìm thấy nút Đăng", message)
        self.assertIn("YouTube Shorts: Bỏ qua - Phát hiện bản quyền", message)
        self.assertIn("1 thành công, 1 bỏ qua, 1 thất bại", message)


    @patch("services.telegram_service.is_telegram_quiet_hours_active", return_value=False)
    @patch("services.telegram_service._ensure_bot")
    def test_diagnostic_sends_screenshot_and_metadata(
        self, ensure_bot, _quiet_hours
    ):
        bot = FakeTelegramBot()
        ensure_bot.return_value = bot
        with tempfile.TemporaryDirectory() as directory:
            screenshot = Path(directory) / "screenshot.png"
            metadata = Path(directory) / "diagnostic.json"
            screenshot.write_bytes(b"png")
            metadata.write_text("{}", encoding="utf-8")
            telegram_service.send_diagnostic_notification(
                {
                    "profile_id": "2",
                    "video_id": "123",
                    "platform": "youtube",
                    "occurred_at": "2026-07-16T15:00:00",
                    "url": "https://studio.youtube.com/upload",
                    "error": "chooser timeout",
                    "last_response": {"status": 500, "url": "https://example/api"},
                    "screenshot_path": str(screenshot),
                    "metadata_path": str(metadata),
                }
            )

        self.assertEqual(len(bot.photos), 1)
        self.assertEqual(len(bot.documents), 1)
        self.assertIn("Profile: 2", bot.photos[0][1]["caption"])
        self.assertIn("YouTube Shorts", bot.photos[0][1]["caption"])


if __name__ == "__main__":
    unittest.main()
