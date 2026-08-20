import unittest
from unittest.mock import Mock, patch

from services.assistant.ai_assistant_service import AiAssistantError, AiAssistantService


class AiAssistantServiceTests(unittest.TestCase):
    def test_caption_generation_uses_chat_model_and_cleans_wrapper_text(self):
        service = AiAssistantService()
        with patch.object(
            service,
            "chat",
            return_value={
                "reply": '```text\nMô tả: "Pha xử lý cực cháy #game"\n```',
                "provider": "gemini",
                "model": "dyna-caption-model",
            },
        ) as chat:
            result = service.generate_caption(
                original_description="Pha xử lý Ma Chao",
                instruction="Viết vui và giữ hashtag",
                video_label="76620001",
            )

        self.assertEqual(result["caption"], "Pha xử lý cực cháy #game")
        self.assertEqual(result["model"], "dyna-caption-model")
        messages, context = chat.call_args.args
        self.assertIn("Pha xử lý Ma Chao", messages[0]["content"])
        self.assertEqual(context["mode"], "social_caption_generation")

    def test_caption_generation_unwraps_nested_reply_actions_json(self):
        service = AiAssistantService()
        with patch.object(
            service,
            "chat",
            return_value={
                "reply": (
                    '```json\n{"reply": "Thái độ của bạn ngày hôm đó, tôi đều khắc cốt ghi tâm.", '
                    '"actions": []}\n```'
                ),
                "provider": "gemini",
                "model": "dyna-caption-model",
            },
        ):
            result = service.generate_caption(
                original_description="Mô tả gốc",
                instruction="Viết lại bằng tiếng Việt",
                video_label="76620001",
            )

        self.assertEqual(
            result["caption"],
            "Thái độ của bạn ngày hôm đó, tôi đều khắc cốt ghi tâm.",
        )

    def test_caption_generation_recovers_json_with_one_trailing_brace(self):
        service = AiAssistantService()
        with patch.object(
            service,
            "chat",
            return_value={
                "reply": '{"reply": "Chỉ lấy nội dung này.", "actions": []}\n}',
                "provider": "gemini",
                "model": "dyna-caption-model",
            },
        ):
            result = service.generate_caption(
                original_description="Mô tả gốc",
                instruction="Viết lại",
            )

        self.assertEqual(result["caption"], "Chỉ lấy nội dung này.")

    def test_subtitle_translation_preserves_ids_and_parses_json_fence(self):
        service = AiAssistantService()
        with patch.object(
            service,
            "chat",
            return_value={
                "reply": (
                    '```json\n[{"id":"1","text":"Hello"},'
                    '{"id":"2","text":"See you again"}]\n```'
                )
            },
        ) as chat:
            result = service.translate_subtitles(
                [
                    {"id": "1", "text": "Xin chào"},
                    {"id": "2", "text": "Hẹn gặp lại"},
                ],
                source_language="vi",
                target_language="en",
            )

        self.assertEqual(
            result,
            [
                {"id": "1", "text": "Hello"},
                {"id": "2", "text": "See you again"},
            ],
        )
        _messages, context = chat.call_args.args
        self.assertEqual(context["mode"], "subtitle_translation")
        self.assertEqual(context["line_count"], 2)

    def test_subtitle_translation_rejects_missing_lines(self):
        service = AiAssistantService()
        with patch.object(
            service,
            "chat",
            return_value={"reply": '[{"id":"1","text":"Hello"}]'},
        ):
            with self.assertRaises(AiAssistantError):
                service.translate_subtitles(
                    [
                        {"id": "1", "text": "Xin chào"},
                        {"id": "2", "text": "Hẹn gặp lại"},
                    ],
                    source_language="vi",
                    target_language="en",
                )

    def test_subtitle_translation_unwraps_nested_gateway_reply(self):
        service = AiAssistantService()
        with patch.object(
            service,
            "chat",
            return_value={
                "reply": (
                    '{"reply":"[{\\"id\\":\\"1\\",\\"translation\\":\\"Xin chào\\"},'
                    '{\\"id\\":\\"2\\",\\"translation\\":\\"Tạm biệt\\"}]",'
                    '"actions":[]}'
                )
            },
        ):
            result = service.translate_subtitles(
                [
                    {"id": "1", "text": "你好"},
                    {"id": "2", "text": "再见"},
                ],
                source_language="zh",
                target_language="vi",
            )

        self.assertEqual(
            result,
            [{"id": "1", "text": "Xin chào"}, {"id": "2", "text": "Tạm biệt"}],
        )

    def test_subtitle_translation_repairs_plain_text_response_with_delimiters(self):
        service = AiAssistantService()
        with patch.object(
            service,
            "chat",
            side_effect=[
                {"reply": "Tôi đã dịch phụ đề cho bạn."},
                {"reply": "1|||Xin chào\n2|||Tạm biệt"},
            ],
        ) as chat:
            result = service.translate_subtitles(
                [
                    {"id": "1", "text": "你好"},
                    {"id": "2", "text": "再见"},
                ],
                source_language="zh",
                target_language="vi",
            )

        self.assertEqual(
            result,
            [{"id": "1", "text": "Xin chào"}, {"id": "2", "text": "Tạm biệt"}],
        )
        self.assertEqual(chat.call_count, 2)

    def test_subtitle_translation_only_retries_ids_missing_from_partial_reply(self):
        service = AiAssistantService()
        with patch.object(
            service,
            "chat",
            side_effect=[
                {"reply": "1|||Xin chào\n3|||Hẹn gặp lại"},
                {"reply": "2|||Cảm ơn"},
            ],
        ) as chat:
            result = service.translate_subtitles(
                [
                    {"id": "1", "text": "你好"},
                    {"id": "2", "text": "谢谢"},
                    {"id": "3", "text": "再见"},
                ],
                source_language="zh",
                target_language="vi",
            )

        self.assertEqual(
            result,
            [
                {"id": "1", "text": "Xin chào"},
                {"id": "2", "text": "Cảm ơn"},
                {"id": "3", "text": "Hẹn gặp lại"},
            ],
        )
        retry_messages, retry_context = chat.call_args_list[1].args
        self.assertIn("2|||谢谢", retry_messages[0]["content"])
        self.assertNotIn("1|||你好", retry_messages[0]["content"])
        self.assertEqual(retry_context["line_count"], 1)

    @patch("services.assistant.ai_assistant_service.requests.post")
    @patch("services.assistant.ai_assistant_service.auth_service.get_current_user")
    def test_proposal_is_normalized_bound_to_user_and_single_use(self, get_user, post):
        get_user.return_value = {
            "username": "tester",
            "token": "private-session-token",
        }
        response = Mock()
        response.status_code = 200
        response.headers = {}
        response.json.return_value = {
            "reply": "Mình đề xuất chạy hồ sơ.",
            "provider": "groq",
            "model": "test-model",
            "actions": [
                {"type": "delete_profile", "args": {"profile_id": "1"}},
                {"type": "start_profile", "args": {"profile_id": "not-a-number"}},
                {"type": "start_profile", "args": {"profile_id": "2", "extra": "ignored"}},
            ],
        }
        post.return_value = response
        service = AiAssistantService()

        result = service.chat([{"role": "user", "content": "Chạy hồ sơ 2"}], {"profiles": []})

        self.assertEqual(result["actions"], [{"type": "start_profile", "args": {"profile_id": "2"}}])
        self.assertTrue(result["proposal_token"])
        outbound = post.call_args.kwargs
        self.assertNotIn("private-session-token", str(outbound["json"]))
        self.assertEqual(
            service.consume_proposal(result["proposal_token"]),
            [{"type": "start_profile", "args": {"profile_id": "2"}}],
        )
        with self.assertRaises(AiAssistantError) as caught:
            service.consume_proposal(result["proposal_token"])
        self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
