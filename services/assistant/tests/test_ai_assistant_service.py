import unittest
from unittest.mock import Mock, patch

from services.assistant.ai_assistant_service import AiAssistantError, AiAssistantService


class AiAssistantServiceTests(unittest.TestCase):
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
