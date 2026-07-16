import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from profile_automation.pipeline import profile_worker
from services import busy_mode_service, telegram_service


class BusyModeServiceTests(unittest.TestCase):
    def test_busy_mode_is_persisted_and_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "busy_mode.json"
            with patch.object(busy_mode_service, "BUSY_MODE_FILE", state_file):
                self.assertFalse(busy_mode_service.is_busy_mode_enabled())
                enabled = busy_mode_service.set_busy_mode(True, source="test")
                self.assertTrue(enabled["busy"])
                self.assertTrue(busy_mode_service.is_busy_mode_enabled())
                self.assertTrue(json.loads(state_file.read_text(encoding="utf-8"))["busy"])

                busy_mode_service.set_busy_mode(False, source="test")
                self.assertFalse(busy_mode_service.is_busy_mode_enabled())

    @patch.object(profile_worker, "request_caption_for_video")
    @patch.object(profile_worker, "is_busy_mode_enabled", return_value=True)
    def test_busy_pipeline_uses_default_caption_without_telegram(self, _busy, request_caption):
        video = SimpleNamespace(aweme_id="123", desc="Mô tả gốc")
        profile = {
            "default_caption": "Mô tả mặc định #valorant",
            "tiktok": {"enabled": True, "use_original_desc": False},
        }

        caption = profile_worker.resolve_runtime_caption(profile, "2", video)

        self.assertEqual(caption, "Mô tả mặc định #valorant")
        request_caption.assert_not_called()


class BusyModeTelegramTests(unittest.TestCase):
    def setUp(self):
        self.original_bot = telegram_service.bot
        telegram_service.bot = Mock()
        self.message = SimpleNamespace(chat=SimpleNamespace(id=6267260356))

    def tearDown(self):
        telegram_service.bot = self.original_bot

    @patch.object(telegram_service.config, "TELEGRAM_CHAT_ID", "6267260356")
    @patch.object(telegram_service.busy_mode_service, "set_busy_mode")
    def test_busy_command_enables_busy_mode(self, set_busy_mode):
        telegram_service.cmd_busy(self.message)

        set_busy_mode.assert_called_once_with(True, source="telegram:/busy")
        telegram_service.bot.reply_to.assert_called_once()

    @patch.object(telegram_service.config, "TELEGRAM_CHAT_ID", "6267260356")
    @patch.object(telegram_service.busy_mode_service, "set_busy_mode")
    def test_free_command_disables_busy_mode(self, set_busy_mode):
        telegram_service.cmd_free(self.message)

        set_busy_mode.assert_called_once_with(False, source="telegram:/free")
        telegram_service.bot.reply_to.assert_called_once()

    @patch.object(telegram_service.config, "TELEGRAM_CHAT_ID", "6267260356")
    @patch.object(telegram_service.busy_mode_service, "set_busy_mode")
    def test_busy_command_ignores_other_chats(self, set_busy_mode):
        telegram_service.cmd_busy(SimpleNamespace(chat=SimpleNamespace(id=999)))

        set_busy_mode.assert_not_called()
        telegram_service.bot.reply_to.assert_not_called()

    def test_enabling_busy_releases_a_pending_caption_request(self):
        fake_bot = Mock()
        fake_bot.send_message.return_value = SimpleNamespace(message_id=77)
        video = SimpleNamespace(
            aweme_id="123",
            share_url="https://www.douyin.com/video/123",
            desc="Mô tả gốc",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            requests_file = Path(temp_dir) / "requests.json"
            mappings_file = Path(temp_dir) / "mappings.json"
            with (
                patch.object(telegram_service, "CAPTION_REQUESTS_FILE", str(requests_file)),
                patch.object(telegram_service, "CAPTION_MESSAGE_MAP_FILE", str(mappings_file)),
                patch.object(telegram_service, "_ensure_bot", return_value=fake_bot),
                patch.object(telegram_service, "ensure_telegram_listener_running"),
                patch.object(telegram_service, "is_telegram_quiet_hours_active", return_value=False),
                patch.object(
                    telegram_service.busy_mode_service,
                    "is_busy_mode_enabled",
                    side_effect=[False, True],
                ),
            ):
                caption = telegram_service.request_caption_for_video(
                    "2",
                    video,
                    default_caption="Mô tả mặc định",
                    timeout_sec=30,
                )

            request = json.loads(requests_file.read_text(encoding="utf-8"))["2:123"]
            self.assertEqual(caption, "Mô tả mặc định")
            self.assertEqual(request["status"], "resolved")
            self.assertEqual(request["action"], "busy_mode")


if __name__ == "__main__":
    unittest.main()
