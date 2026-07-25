import json
import unittest

import httpx

from app.config import Settings
from app.services.ai_gateway_service import (
    AiGatewayBadRequestError,
    AiGatewayConfigurationError,
    AiGatewayService,
)


def settings(**overrides):
    values = {
        "AI_ENABLED": True,
        "AI_REQUIRE_ACTIVE_LICENSE": False,
        "AI_PROVIDER_ORDER": "gemini,groq,openrouter",
        "AI_GEMINI_API_KEYS": "",
        "AI_GROQ_API_KEYS": "",
        "AI_OPENROUTER_API_KEYS": "",
        "AI_REQUESTS_PER_MINUTE": 20,
    }
    values.update(overrides)
    return Settings(**values)


class AiGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_rotates_provider_after_rate_limit(self):
        calls = []

        def handler(request: httpx.Request):
            calls.append(str(request.url))
            if "googleapis.com" in str(request.url):
                return httpx.Response(429, headers={"Retry-After": "5"}, json={"error": "rate limit"})
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps({"reply": "Đã chuyển provider", "actions": []})}}],
                    "usage": {"total_tokens": 17},
                },
            )

        transport = httpx.MockTransport(handler)
        gateway = AiGatewayService(
            settings(
                AI_GEMINI_API_KEYS="gemini-key",
                AI_GROQ_API_KEYS="groq-key",
            ),
            client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
        )

        result = await gateway.chat(
            "tester",
            [{"role": "user", "content": "Kiểm tra Dyna"}],
            {"profiles": []},
        )

        self.assertEqual(result["provider"], "groq")
        self.assertEqual(result["reply"], "Đã chuyển provider")
        self.assertEqual(len(calls), 2)

    async def test_rotates_to_second_key_of_same_provider(self):
        keys = []

        def handler(request: httpx.Request):
            key = request.headers.get("x-goog-api-key")
            keys.append(key)
            if key == "first-key":
                return httpx.Response(503, json={"error": "temporarily unavailable"})
            return httpx.Response(
                200,
                json={
                    "candidates": [{"content": {"parts": [{"text": '{"reply":"OK","actions":[]}' }]}}],
                    "usageMetadata": {"totalTokenCount": 8},
                },
            )

        transport = httpx.MockTransport(handler)
        gateway = AiGatewayService(
            settings(AI_GEMINI_API_KEYS="first-key,second-key", AI_PROVIDER_ORDER="gemini"),
            client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
        )

        result = await gateway.chat("tester", [{"role": "user", "content": "Ping"}], {})

        self.assertEqual(result["reply"], "OK")
        self.assertEqual(keys, ["first-key", "second-key"])

    async def test_singular_key_alias_is_supported(self):
        seen = []

        def handler(request: httpx.Request):
            seen.append(request.headers.get("authorization"))
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"reply":"OK","actions":[]}'}}],
                },
            )

        gateway = AiGatewayService(
            settings(AI_GROQ_API_KEY="single-key", AI_PROVIDER_ORDER="groq"),
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), **kwargs
            ),
        )

        result = await gateway.chat("tester", [{"role": "user", "content": "Ping"}], {})

        self.assertEqual(result["reply"], "OK")
        self.assertEqual(seen, ["Bearer single-key"])

    async def test_quota_error_body_cools_down_slot_and_rotates(self):
        calls = []

        def handler(request: httpx.Request):
            calls.append(str(request.url))
            if "groq.com" in str(request.url):
                return httpx.Response(200, json={"error": {"code": "quota_exceeded"}})
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"reply":"fallback","actions":[]}'}}],
                },
            )

        gateway = AiGatewayService(
            settings(
                AI_GROQ_API_KEYS="groq-key",
                AI_OPENROUTER_API_KEYS="router-key",
                AI_PROVIDER_ORDER="groq,openrouter",
            ),
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), **kwargs
            ),
        )

        result = await gateway.chat("tester", [{"role": "user", "content": "Ping"}], {})

        self.assertEqual(result["provider"], "openrouter")
        self.assertEqual(len(calls), 2)

    async def test_bad_request_does_not_spend_other_provider(self):
        calls = 0

        def handler(_request: httpx.Request):
            nonlocal calls
            calls += 1
            return httpx.Response(400, json={"error": "invalid request"})

        gateway = AiGatewayService(
            settings(AI_GEMINI_API_KEYS="one", AI_GROQ_API_KEYS="two"),
            client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs),
        )

        with self.assertRaises(AiGatewayBadRequestError):
            await gateway.chat("tester", [{"role": "user", "content": "Ping"}], {})
        self.assertEqual(calls, 1)

    async def test_unapproved_actions_are_removed(self):
        def handler(_request: httpx.Request):
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps({
                        "reply": "Không thực thi trực tiếp",
                        "actions": [
                            {"type": "delete_profile", "args": {"profile_id": "1"}},
                            {"type": "set_busy_mode", "args": {"busy": True}},
                        ],
                    })}}],
                },
            )

        gateway = AiGatewayService(
            settings(AI_GROQ_API_KEYS="groq", AI_PROVIDER_ORDER="groq"),
            client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs),
        )
        result = await gateway.chat("tester", [{"role": "user", "content": "Xóa Profile 1"}], {})

        self.assertEqual(result["actions"], [])

    async def test_double_encoded_json_is_unwrapped(self):
        encoded = json.dumps(json.dumps({
            "reply": "Đã chuẩn bị yêu cầu",
            "actions": [{"type": "start_profile", "args": {"profile_id": "2"}}],
        }, ensure_ascii=False), ensure_ascii=False)

        def handler(_request: httpx.Request):
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": encoded}]}}]},
            )

        gateway = AiGatewayService(
            settings(AI_GEMINI_API_KEY="gemini", AI_PROVIDER_ORDER="gemini"),
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), **kwargs
            ),
        )

        result = await gateway.chat("tester", [{"role": "user", "content": "Chạy hồ sơ 2"}], {})

        self.assertEqual(result["reply"], "Đã chuẩn bị yêu cầu")
        self.assertEqual(result["actions"][0]["type"], "start_profile")

    async def test_protocol_nested_inside_reply_is_unwrapped(self):
        nested = json.dumps({
            "reply": "Vui lòng xác nhận hành động này.",
            "actions": [{"type": "start_profile", "args": {"profile_id": "2"}}],
        }, ensure_ascii=False, indent=2)
        outer = json.dumps({"reply": nested, "actions": []}, ensure_ascii=False)

        def handler(_request: httpx.Request):
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": outer}]}}]},
            )

        gateway = AiGatewayService(
            settings(AI_GEMINI_API_KEY="gemini", AI_PROVIDER_ORDER="gemini"),
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), **kwargs
            ),
        )

        result = await gateway.chat("tester", [{"role": "user", "content": "Chạy hồ sơ 2"}], {})

        self.assertEqual(result["reply"], "Vui lòng xác nhận hành động này.")
        self.assertEqual(result["actions"][0]["args"], {"profile_id": "2"})

    async def test_missing_keys_reports_configuration_error(self):
        gateway = AiGatewayService(settings())
        with self.assertRaises(AiGatewayConfigurationError):
            await gateway.chat("tester", [{"role": "user", "content": "Ping"}], {})


if __name__ == "__main__":
    unittest.main()
