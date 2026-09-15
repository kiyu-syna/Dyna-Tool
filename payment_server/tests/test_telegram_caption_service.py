import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

from app.services import telegram_bot_service


class TelegramCaptionServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = telegram_bot_service.TelegramBotService()
        self.db = SimpleNamespace(
            telegram_links=SimpleNamespace(find_one=AsyncMock()),
            telegram_link_codes=SimpleNamespace(
                find_one=AsyncMock(),
                delete_many=AsyncMock(),
            ),
            telegram_caption_requests=SimpleNamespace(
                find_one=AsyncMock(),
                insert_one=AsyncMock(),
                update_one=AsyncMock(),
            ),
        )

    async def test_caption_request_is_pinned_and_has_no_expiry(self):
        self.db.telegram_links.find_one.return_value = {
            "chat_id": "12345",
        }
        self.service._api = AsyncMock(
            side_effect=[
                {"message_id": 77},
                True,
            ]
        )

        with patch.object(telegram_bot_service, "get_db", return_value=self.db):
            result = await self.service.create_caption_request(
                "tester",
                {
                    "profile_id": "2",
                    "profile_name": "Profile 2",
                    "source_label": "Nguồn A",
                    "video_id": "video-1",
                    "description": "Mô tả nguồn",
                    "default_caption": "Caption mặc định",
                    "pin_message": True,
                },
            )

        stored = self.db.telegram_caption_requests.insert_one.await_args.args[0]
        self.assertNotIn("expires_at", stored)
        self.assertTrue(stored["pinned"])
        self.assertTrue(result["pinned"])
        self.assertEqual(self.service._api.await_args_list[1].args[0], "pinChatMessage")

    async def test_diagnostic_image_is_sent_as_telegram_photo(self):
        self.db.telegram_links.find_one.return_value = {"chat_id": "12345"}
        self.service._api_multipart = AsyncMock(return_value={"message_id": 78})

        with (
            patch.object(telegram_bot_service, "get_db", return_value=self.db),
            patch.object(
                telegram_bot_service.TelegramBotService,
                "configured",
                new_callable=PropertyMock,
                return_value=True,
            ),
        ):
            delivered = await self.service.send_diagnostic_image(
                "tester",
                "Lỗi TikTok",
                b"png",
                "screenshot.png",
            )

        self.assertTrue(delivered)
        self.assertEqual(self.service._api_multipart.await_args.args[0], "sendPhoto")
        self.assertEqual(
            self.service._api_multipart.await_args.kwargs["files"]["photo"],
            ("screenshot.png", b"png", "image/png"),
        )

    async def test_only_reply_to_prompt_selects_caption_and_unpins_it(self):
        self.db.telegram_caption_requests.find_one.return_value = {
            "_id": "mongo-id",
            "request_id": "caption-request-1",
            "pinned": True,
        }
        self.db.telegram_caption_requests.update_one.return_value = SimpleNamespace(
            modified_count=1
        )
        self.service._api = AsyncMock(return_value=True)

        with patch.object(telegram_bot_service, "get_db", return_value=self.db):
            await self.service._handle_message(
                {
                    "message_id": 88,
                    "text": "Caption do người dùng nhập",
                    "chat": {"id": "12345"},
                    "reply_to_message": {"message_id": 77},
                }
            )

        update = self.db.telegram_caption_requests.update_one.await_args.args[1]
        self.assertEqual(update["$set"]["status"], "selected")
        self.assertEqual(
            update["$set"]["caption"],
            "Caption do người dùng nhập",
        )
        self.assertEqual(self.service._api.await_args_list[0].args[0], "unpinChatMessage")

    async def test_non_reply_message_does_not_select_caption(self):
        self.service._api = AsyncMock(return_value=True)

        with patch.object(telegram_bot_service, "get_db", return_value=self.db):
            await self.service._handle_message(
                {
                    "message_id": 88,
                    "text": "Tin nhắn bình thường",
                    "chat": {"id": "12345"},
                }
            )

        self.db.telegram_caption_requests.find_one.assert_not_awaited()
        self.db.telegram_caption_requests.update_one.assert_not_awaited()

    async def test_start_relinks_chat_from_previous_account(self):
        self.db.telegram_link_codes.find_one.return_value = {
            "username": "new-account",
        }
        self.db.telegram_links.find_one.return_value = {
            "username": "old-account",
            "chat_id": "12345",
        }
        self.db.telegram_links.delete_one = AsyncMock()
        self.db.telegram_links.update_one = AsyncMock()
        self.service._api = AsyncMock(return_value=True)

        with patch.object(telegram_bot_service, "get_db", return_value=self.db):
            await self.service._handle_message(
                {
                    "text": "/start LINKCODE",
                    "chat": {"id": "12345", "title": "Test"},
                    "from": {"first_name": "Tester"},
                }
            )

        self.db.telegram_links.delete_one.assert_awaited_once_with({"chat_id": "12345"})
        self.db.telegram_links.update_one.assert_awaited_once()
        self.assertIn("Đã liên kết", self.service._api.await_args.args[1]["text"])


if __name__ == "__main__":
    unittest.main()
