"""One-time, Telegram-confirmed actions delivered to an online Dyna desktop."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ReturnDocument

from app.database.mongodb import get_db

ACTION_TTL_MINUTES = 5
CLAIM_TTL_SECONDS = 90
SUPPORTED_ACTIONS = {
    "start_profile",
    "stop_profile",
    "refresh_readiness",
    "cancel_video",
}
SUPPORTED_PLATFORMS = {"youtube", "tiktok", "facebook"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _profile_id(value: Any) -> str:
    profile_id = str(value or "").strip()
    if not re.fullmatch(r"\d+", profile_id):
        raise ValueError("Hồ sơ không hợp lệ")
    return profile_id


def normalize_action(raw: Any) -> dict[str, Any] | None:
    """Validate actions again at the server boundary before storing them."""
    if not isinstance(raw, dict):
        return None
    action_type = str(raw.get("type") or "").strip()
    args = raw.get("args")
    if action_type not in SUPPORTED_ACTIONS or not isinstance(args, dict):
        return None
    try:
        if action_type in {"start_profile", "stop_profile"}:
            clean_args = {"profile_id": _profile_id(args.get("profile_id"))}
        elif action_type == "cancel_video":
            video_id = str(args.get("video_id") or "").strip()
            if not video_id or len(video_id) > 256:
                return None
            clean_args = {"profile_id": _profile_id(args.get("profile_id")), "video_id": video_id}
        else:
            targets: list[dict[str, Any]] = []
            seen: set[str] = set()
            raw_targets = args.get("targets")
            if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= 50:
                return None
            for target in raw_targets:
                if not isinstance(target, dict):
                    return None
                profile_id = _profile_id(target.get("profile_id"))
                platforms = list(dict.fromkeys(
                    str(platform or "").strip().lower()
                    for platform in (target.get("platforms") or [])
                ))
                if profile_id in seen or not platforms or any(platform not in SUPPORTED_PLATFORMS for platform in platforms):
                    return None
                seen.add(profile_id)
                targets.append({"profile_id": profile_id, "platforms": platforms})
            clean_args = {"targets": targets}
    except ValueError:
        return None
    return {"type": action_type, "args": clean_args}


def action_label(action: dict[str, Any]) -> str:
    action_type = action["type"]
    args = action["args"]
    if action_type == "start_profile":
        return f"Khởi chạy hồ sơ {args['profile_id']}"
    if action_type == "stop_profile":
        return f"Dừng hồ sơ {args['profile_id']}"
    if action_type == "cancel_video":
        return f"Huỷ video {args['video_id']} ở hồ sơ {args['profile_id']}"
    return f"Kiểm tra sẵn sàng cho {len(args['targets'])} hồ sơ"


class TelegramRemoteActionService:
    async def create(self, username: str, chat_id: str, actions: list[Any]) -> dict[str, Any] | None:
        safe_actions = [clean for item in actions if (clean := normalize_action(item)) is not None][:8]
        if not safe_actions:
            return None
        now = _now()
        request_id = secrets.token_urlsafe(18)
        document = {
            "request_id": request_id,
            "username": username.strip().lower(),
            "chat_id": str(chat_id),
            "actions": safe_actions,
            "status": "pending",
            "created_at": now,
            "expires_at": now + timedelta(minutes=ACTION_TTL_MINUTES),
        }
        await get_db().telegram_remote_actions.insert_one(document)
        return document

    async def confirm(self, username: str, chat_id: str, request_id: str) -> dict[str, Any] | None:
        now = _now()
        return await get_db().telegram_remote_actions.find_one_and_update(
            {
                "request_id": request_id,
                "username": username.strip().lower(),
                "chat_id": str(chat_id),
                "status": "pending",
                "expires_at": {"$gt": now},
            },
            {"$set": {"status": "confirmed", "confirmed_at": now}},
            return_document=ReturnDocument.AFTER,
        )

    async def cancel(self, username: str, chat_id: str, request_id: str) -> bool:
        result = await get_db().telegram_remote_actions.update_one(
            {
                "request_id": request_id,
                "username": username.strip().lower(),
                "chat_id": str(chat_id),
                "status": "pending",
            },
            {"$set": {"status": "cancelled", "cancelled_at": _now()}},
        )
        return result.modified_count == 1

    async def claim_next(self, username: str) -> dict[str, Any] | None:
        now = _now()
        return await get_db().telegram_remote_actions.find_one_and_update(
            {
                "username": username.strip().lower(),
                "expires_at": {"$gt": now},
                "$or": [
                    {"status": "confirmed"},
                    {"status": "claimed", "claim_expires_at": {"$lte": now}},
                ],
            },
            {"$set": {
                "status": "claimed",
                "claimed_at": now,
                "claim_expires_at": now + timedelta(seconds=CLAIM_TTL_SECONDS),
            }},
            sort=[("confirmed_at", 1), ("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )

    async def complete(self, username: str, request_id: str, results: list[dict[str, Any]]) -> dict[str, Any] | None:
        now = _now()
        succeeded = bool(results) and all(bool(item.get("ok")) for item in results)
        return await get_db().telegram_remote_actions.find_one_and_update(
            {
                "request_id": request_id,
                "username": username.strip().lower(),
                "status": "claimed",
            },
            {"$set": {
                "status": "completed" if succeeded else "failed",
                "completed_at": now,
                "results": results[:8],
            }},
            return_document=ReturnDocument.AFTER,
        )


_service = TelegramRemoteActionService()


def get_telegram_remote_action_service() -> TelegramRemoteActionService:
    return _service
