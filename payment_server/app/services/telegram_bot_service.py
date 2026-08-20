"""Server-owned shared Telegram bot for Dyna.

The bot token lives exclusively in the payment-server environment. Desktop
clients use their existing Dyna session token and never receive or save it.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import get_settings
from app.database.mongodb import get_db
from app.services.ai_gateway_service import (
    AiGatewayBadRequestError,
    AiGatewayConfigurationError,
    AiGatewayRateLimitError,
    AiGatewayUnavailableError,
    get_ai_gateway,
)
from app.services.license_service import get_license_status
from app.services.telegram_remote_action_service import (
    action_label,
    get_telegram_remote_action_service,
)

logger = logging.getLogger(__name__)
UTC = timezone.utc
AI_HISTORY_LIMIT = 12
AI_MESSAGE_LIMIT = 6_000
TELEGRAM_MESSAGE_LIMIT = 3_900


def _now() -> datetime:
    return datetime.now(UTC)


class TelegramBotService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._offset = 0
        # Short-lived context only. AI conversation content is not persisted in
        # MongoDB and naturally clears when the server restarts.
        self._ai_histories: dict[str, list[dict[str, str]]] = {}

    @property
    def configured(self) -> bool:
        return bool(get_settings().TELEGRAM_BOT_TOKEN.strip())

    async def start(self) -> None:
        settings = get_settings()
        if not (settings.TELEGRAM_POLLING_ENABLED and self.configured):
            logger.info("Telegram shared bot is disabled or not configured")
            return
        self._stopping = False
        self._task = asyncio.create_task(self._poll_loop(), name="dyna-telegram-bot")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _api(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        token = get_settings().TELEGRAM_BOT_TOKEN.strip()
        if not token:
            raise RuntimeError("Telegram shared bot is not configured on the server")
        async with httpx.AsyncClient(timeout=55) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/{method}", json=payload or {}
            )
        data = response.json()
        if not response.is_success or not data.get("ok"):
            raise RuntimeError(str(data.get("description") or "Telegram API request failed"))
        return data.get("result")

    async def connection_status(self, username: str) -> dict[str, Any]:
        link = await get_db().telegram_links.find_one({"username": username})
        settings = get_settings()
        return {
            "configured": self.configured,
            "linked": bool(link),
            "chat_name": str((link or {}).get("chat_name") or ""),
            "linked_at": (link or {}).get("linked_at"),
            "bot_username": settings.TELEGRAM_BOT_USERNAME.strip().lstrip("@"),
        }

    async def create_link_code(self, username: str) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("Bot Telegram chưa được cấu hình trên máy chủ Dyna")
        code = secrets.token_urlsafe(7).replace("-", "").replace("_", "").upper()[:10]
        now = _now()
        expires_at = now + timedelta(minutes=10)
        db = get_db()
        await db.telegram_link_codes.delete_many({"username": username})
        await db.telegram_link_codes.insert_one({"code": code, "username": username, "created_at": now, "expires_at": expires_at})
        return {"code": code, "expires_at": expires_at, "bot_username": get_settings().TELEGRAM_BOT_USERNAME.strip().lstrip("@")}

    async def unlink(self, username: str) -> None:
        await get_db().telegram_links.delete_one({"username": username})

    async def send_notification(self, username: str, text: str, cancel_job: dict[str, str] | None = None) -> bool:
        link = await get_db().telegram_links.find_one({"username": username})
        if not link or not self.configured:
            return False
        payload: dict[str, Any] = {"chat_id": link["chat_id"], "text": text[:4000], "disable_web_page_preview": True}
        profile_id = str((cancel_job or {}).get("profile_id") or "").strip()
        video_id = str((cancel_job or {}).get("video_id") or "").strip()
        if profile_id and video_id:
            payload["reply_markup"] = {"inline_keyboard": [[
                {"text": "Huỷ video", "callback_data": f"job:{profile_id}:{video_id}"},
            ]]}
        await self._api("sendMessage", payload)
        return True

    async def create_caption_request(self, username: str, payload: dict[str, Any]) -> dict[str, Any]:
        link = await get_db().telegram_links.find_one({"username": username})
        if not link:
            return {"available": False, "reason": "Telegram chưa được liên kết"}
        request_id = secrets.token_urlsafe(18)
        now = _now()
        default_caption = str(payload.get("default_caption") or "").strip()
        description = str(payload.get("description") or "").strip()
        text = (
            "DYNA CẦN CAPTION CHO VIDEO\n\n"
            "Hãy Reply chính tin nhắn này và gửi caption bạn muốn dùng. "
            "Dyna sẽ chờ đến khi nhận được reply rồi mới đăng video.\n\n"
            f"Hồ sơ: {payload.get('profile_name') or payload.get('profile_id') or '-'}\n"
            f"Nguồn: {payload.get('source_label') or '-'}\n"
            f"Video: {payload.get('video_id') or '-'}\n\n"
            f"Mô tả nguồn:\n{description or '(trống)'}\n\n"
            f"Caption hiện tại:\n{default_caption or '(trống)'}"
        )
        result = await self._api("sendMessage", {
            "chat_id": link["chat_id"],
            "text": text[:4000],
            "disable_web_page_preview": True,
        })
        pinned = False
        pin_error = ""
        if payload.get("pin_message") is True:
            try:
                await self._api("pinChatMessage", {
                    "chat_id": link["chat_id"],
                    "message_id": int(result["message_id"]),
                    "disable_notification": True,
                })
                pinned = True
            except RuntimeError as exc:
                pin_error = str(exc)
                logger.warning(
                    "Unable to pin Telegram caption request %s: %s",
                    request_id,
                    exc,
                )
        await get_db().telegram_caption_requests.insert_one({
            "request_id": request_id, "username": username, "status": "pending",
            "default_caption": default_caption, "caption": "", "created_at": now,
            "updated_at": now, "pinned": pinned,
            "message_id": int(result["message_id"]), "chat_id": str(link["chat_id"]),
        })
        return {
            "available": True,
            "request_id": request_id,
            "pinned": pinned,
            "pin_error": pin_error,
        }

    async def caption_status(self, username: str, request_id: str) -> dict[str, Any]:
        request = await get_db().telegram_caption_requests.find_one({"username": username, "request_id": request_id})
        if not request:
            return {"status": "missing", "caption": ""}
        return {"status": str(request.get("status") or "pending"), "caption": str(request.get("caption") or "")}

    async def _poll_loop(self) -> None:
        while not self._stopping:
            try:
                updates = await self._api("getUpdates", {"offset": self._offset, "timeout": 45, "allowed_updates": ["message", "callback_query"]})
                for update in updates or []:
                    self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                    await self._handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Telegram polling error: %s", exc)
                await asyncio.sleep(3)

    async def _handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"])
        elif "message" in update:
            await self._handle_message(update["message"])

    async def _send_text(self, chat_id: str, text: str) -> None:
        """Reply safely without exceeding Telegram's message length limit."""
        content = text.strip() or "Dyna AI chưa có phản hồi."
        for start in range(0, len(content), TELEGRAM_MESSAGE_LIMIT):
            await self._api("sendMessage", {
                "chat_id": chat_id,
                "text": content[start:start + TELEGRAM_MESSAGE_LIMIT],
                "disable_web_page_preview": True,
            })

    async def _linked_username(self, chat_id: str) -> str:
        link = await get_db().telegram_links.find_one({"chat_id": chat_id})
        return str((link or {}).get("username") or "").strip().lower()

    async def _handle_ai_message(self, chat_id: str, prompt: str) -> None:
        """Serve Dyna AI in Telegram for an already linked Dyna account.

        Telegram can approve only a small safe action set. The server never
        executes those actions itself: an authenticated, online Dyna desktop
        claims and performs the one-time request.
        """
        username = await self._linked_username(chat_id)
        if not username:
            await self._send_text(
                chat_id,
                "Hãy liên kết Telegram trong Dyna > Cài đặt trước khi dùng Dyna AI.",
            )
            return
        if not prompt:
            await self._send_text(
                chat_id,
                "Dùng: /ai <câu hỏi>\nVí dụ: /ai Tại sao hồ sơ TikTok chưa sẵn sàng?",
            )
            return

        settings = get_settings()
        if settings.AI_REQUIRE_ACTIVE_LICENSE:
            license_status = await get_license_status(username)
            if not license_status.get("is_active"):
                await self._send_text(
                    chat_id,
                    "Trợ lý AI yêu cầu gói Dyna Premium còn hiệu lực.",
                )
                return

        try:
            await self._api("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except Exception:
            # The typing indicator is optional; an API hiccup must not prevent
            # the actual AI response from being generated.
            logger.debug("Unable to send Telegram typing indicator", exc_info=True)
        history = self._ai_histories.get(username, [])
        messages = [*history, {"role": "user", "content": prompt[:AI_MESSAGE_LIMIT]}]
        try:
            result = await get_ai_gateway().chat(
                username=username,
                messages=messages,
                context={"channel": "telegram", "telegram": "linked"},
            )
        except AiGatewayRateLimitError as exc:
            await self._send_text(chat_id, f"Dyna AI đang nhận quá nhiều yêu cầu. Vui lòng thử lại sau {exc.retry_after} giây.")
            return
        except AiGatewayBadRequestError:
            await self._send_text(chat_id, "Yêu cầu chưa hợp lệ. Hãy viết lại câu hỏi ngắn gọn hơn.")
            return
        except AiGatewayConfigurationError:
            logger.warning("Dyna AI is not configured for Telegram user %s", username)
            await self._send_text(chat_id, "Dyna AI chưa được cấu hình trên máy chủ.")
            return
        except AiGatewayUnavailableError:
            logger.warning("Dyna AI is temporarily unavailable for Telegram user %s", username)
            await self._send_text(chat_id, "Dyna AI đang tạm thời không khả dụng. Vui lòng thử lại sau.")
            return
        except Exception:
            logger.exception("Unexpected Dyna AI Telegram error for user %s", username)
            await self._send_text(chat_id, "Không thể lấy phản hồi từ Dyna AI. Vui lòng thử lại sau.")
            return

        assistant_reply = str(result.get("reply") or "Dyna AI chưa có phản hồi.").strip()
        reply = assistant_reply
        self._ai_histories[username] = [
            *messages,
            {"role": "assistant", "content": assistant_reply},
        ][-AI_HISTORY_LIMIT:]
        await self._send_text(chat_id, reply)
        proposal = await get_telegram_remote_action_service().create(
            username,
            chat_id,
            list(result.get("actions") or []),
        )
        if proposal:
            labels = "\n".join(f"• {action_label(action)}" for action in proposal["actions"])
            await self._api("sendMessage", {
                "chat_id": chat_id,
                "text": (
                    "Đề xuất thao tác trong Dyna:\n"
                    f"{labels}\n\n"
                    "Xác nhận trong 5 phút. Dyna desktop phải đang mở và đăng nhập để thực hiện."
                ),
                "reply_markup": {"inline_keyboard": [[
                    {"text": "Xác nhận", "callback_data": f"ai:{proposal['request_id']}:confirm"},
                    {"text": "Hủy", "callback_data": f"ai:{proposal['request_id']}:cancel"},
                ]]},
            })

    async def _handle_message(self, message: dict[str, Any]) -> None:
        text = str(message.get("text") or message.get("caption") or "").strip()
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        if not chat_id:
            return
        command, _, command_argument = text.partition(" ")
        if command.lower().split("@", 1)[0] == "/ai":
            await self._handle_ai_message(chat_id, command_argument.strip())
            return
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            code = parts[1].strip().upper() if len(parts) > 1 else ""
            if not code:
                await self._api("sendMessage", {"chat_id": chat_id, "text": "Mở Dyna > Cài đặt > Telegram để lấy mã liên kết."})
                return
            db = get_db()
            row = await db.telegram_link_codes.find_one({"code": code, "expires_at": {"$gt": _now()}})
            if not row:
                await self._api("sendMessage", {"chat_id": chat_id, "text": "Mã liên kết không hợp lệ hoặc đã hết hạn. Hãy tạo mã mới trong Dyna."})
                return
            await db.telegram_links.update_one({"username": row["username"]}, {"$set": {
                "username": row["username"], "chat_id": chat_id,
                "chat_name": str(chat.get("title") or (message.get("from") or {}).get("first_name") or "Telegram"),
                "linked_at": _now(),
            }}, upsert=True)
            await db.telegram_link_codes.delete_many({"username": row["username"]})
            await self._api("sendMessage", {"chat_id": chat_id, "text": "Đã liên kết Telegram với Dyna. Từ giờ bạn sẽ nhận thông báo tại đây."})
            return
        reply_to = (message.get("reply_to_message") or {}).get("message_id")
        if reply_to and text:
            request = await get_db().telegram_caption_requests.find_one({
                "chat_id": chat_id,
                "message_id": int(reply_to),
                "status": "pending",
            })
            if request:
                result = await get_db().telegram_caption_requests.update_one(
                    {"_id": request["_id"], "status": "pending"},
                    {"$set": {
                        "status": "selected",
                        "caption": text[:10000],
                        "updated_at": _now(),
                    }},
                )
                if result.modified_count == 1:
                    if request.get("pinned"):
                        try:
                            await self._api("unpinChatMessage", {
                                "chat_id": chat_id,
                                "message_id": int(reply_to),
                            })
                        except RuntimeError:
                            logger.warning(
                                "Unable to unpin completed Telegram caption request %s",
                                request.get("request_id"),
                            )
                    try:
                        await self._api("sendMessage", {
                            "chat_id": chat_id,
                            "text": "Dyna đã nhận caption và sẽ tiếp tục đăng video.",
                            "reply_to_message_id": int(message.get("message_id") or 0),
                        })
                    except RuntimeError:
                        logger.warning(
                            "Unable to acknowledge Telegram caption request %s",
                            request.get("request_id"),
                        )

    async def _handle_callback(self, call: dict[str, Any]) -> None:
        parts = str(call.get("data") or "").split(":", 2)
        message = call.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id") or "")
        if len(parts) == 3 and parts[0] == "ai":
            await self._handle_remote_action_callback(call, chat_id, parts[1], parts[2])
            return
        if len(parts) == 3 and parts[0] == "job":
            await self._handle_job_cancel_callback(call, chat_id, parts[1], parts[2])
            return
        if len(parts) != 3 or parts[0] != "cap":
            return
        request_id, action = parts[1], parts[2]
        request = await get_db().telegram_caption_requests.find_one({"request_id": request_id, "chat_id": chat_id, "status": "pending"})
        if not request:
            answer = "Yêu cầu này đã được xử lý hoặc hết hạn."
        else:
            update = {"status": "skipped", "caption": ""} if action == "skip" else {"status": "selected", "caption": str(request.get("default_caption") or "")}
            await get_db().telegram_caption_requests.update_one({"_id": request["_id"]}, {"$set": {**update, "updated_at": _now()}})
            answer = "Đã ghi nhận caption."
        await self._api("answerCallbackQuery", {"callback_query_id": call["id"], "text": answer})

    async def _handle_job_cancel_callback(self, call: dict[str, Any], chat_id: str, profile_id: str, video_id: str) -> None:
        username = await self._linked_username(chat_id)
        answer = "Telegram này chưa được liên kết với Dyna."
        if username:
            proposal = await get_telegram_remote_action_service().create(
                username,
                chat_id,
                [{"type": "cancel_video", "args": {"profile_id": profile_id, "video_id": video_id}}],
            )
            if proposal and await get_telegram_remote_action_service().confirm(username, chat_id, proposal["request_id"]):
                answer = "Đã yêu cầu huỷ video. Dyna sẽ xử lý khi đang trực tuyến."
            else:
                answer = "Không thể yêu cầu huỷ video."
        await self._api("answerCallbackQuery", {"callback_query_id": call["id"], "text": answer})

    async def _handle_remote_action_callback(
        self,
        call: dict[str, Any],
        chat_id: str,
        request_id: str,
        decision: str,
    ) -> None:
        username = await self._linked_username(chat_id)
        if not username:
            answer = "Telegram này chưa được liên kết với Dyna."
        elif decision == "confirm":
            action = await get_telegram_remote_action_service().confirm(username, chat_id, request_id)
            if action:
                answer = "Đã xác nhận. Dyna desktop sẽ thực hiện khi đang trực tuyến."
                await self._send_text(
                    chat_id,
                    "Đã xác nhận thao tác. Dyna sẽ thực hiện ngay khi ứng dụng desktop đang mở và đăng nhập.",
                )
            else:
                answer = "Lệnh đã hết hạn hoặc đã được xử lý."
        elif decision == "cancel":
            cancelled = await get_telegram_remote_action_service().cancel(username, chat_id, request_id)
            answer = "Đã hủy thao tác." if cancelled else "Lệnh đã hết hạn hoặc đã được xử lý."
        else:
            answer = "Thao tác không hợp lệ."
        await self._api("answerCallbackQuery", {"callback_query_id": call["id"], "text": answer})


_service = TelegramBotService()


def get_telegram_bot_service() -> TelegramBotService:
    return _service
