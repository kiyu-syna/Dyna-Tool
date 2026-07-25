"""Desktop bridge to the server-hosted Dyna AI gateway."""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests

import services.account.auth_service as auth_service
import services.account.license_service as license_service


SUPPORTED_ACTIONS = {
    "start_profile",
    "stop_profile",
    "refresh_readiness",
}
SUPPORTED_PLATFORMS = {"youtube", "tiktok", "facebook"}
PROPOSAL_TTL_SECONDS = 10 * 60


class AiAssistantError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503, retry_after: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


@dataclass
class _Proposal:
    username: str
    actions: list[dict[str, Any]]
    expires_at: float


def _safe_detail(response: requests.Response, fallback: str) -> str:
    try:
        detail = response.json().get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    except (ValueError, AttributeError, TypeError):
        pass
    return fallback


def _profile_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not re.fullmatch(r"\d+", normalized):
        raise ValueError("Profile ID không hợp lệ")
    return normalized


def _normalize_action(action: Any) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        return None
    action_type = str(action.get("type") or "").strip()
    args = action.get("args")
    if action_type not in SUPPORTED_ACTIONS or not isinstance(args, dict):
        return None
    try:
        if action_type in {"start_profile", "stop_profile"}:
            clean_args = {"profile_id": _profile_id(args.get("profile_id"))}
        else:
            raw_targets = args.get("targets")
            if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= 50:
                return None
            targets: list[dict[str, Any]] = []
            seen: set[str] = set()
            for target in raw_targets:
                if not isinstance(target, dict):
                    return None
                profile_id = _profile_id(target.get("profile_id"))
                platforms = list(
                    dict.fromkeys(
                        str(platform or "").strip().lower()
                        for platform in (target.get("platforms") or [])
                    )
                )
                if profile_id in seen or not platforms or any(
                    platform not in SUPPORTED_PLATFORMS for platform in platforms
                ):
                    return None
                seen.add(profile_id)
                targets.append({"profile_id": profile_id, "platforms": platforms})
            clean_args = {"targets": targets}
    except ValueError:
        return None
    return {"type": action_type, "args": clean_args}


class AiAssistantService:
    def __init__(self) -> None:
        self._proposals: dict[str, _Proposal] = {}
        self._lock = threading.RLock()

    def chat(self, messages: list[dict[str, str]], context: dict[str, Any]) -> dict[str, Any]:
        user = auth_service.get_current_user()
        if not user or not user.get("token") or not user.get("username"):
            raise AiAssistantError("Cần đăng nhập Dyna để dùng Trợ lý AI", status_code=401)
        try:
            response = requests.post(
                f"{license_service.get_api_base_url()}/api/ai/chat",
                headers={"Authorization": f"Bearer {user['token']}"},
                json={"messages": messages[-20:], "context": context},
                timeout=55,
            )
        except requests.Timeout as exc:
            raise AiAssistantError("Trợ lý AI phản hồi quá lâu. Vui lòng thử lại.") from exc
        except requests.ConnectionError as exc:
            raise AiAssistantError("Không kết nối được máy chủ Dyna AI") from exc
        except requests.RequestException as exc:
            raise AiAssistantError("Không gửi được yêu cầu tới Dyna AI") from exc

        if response.status_code >= 400:
            retry_after = None
            try:
                retry_after = int(response.headers.get("Retry-After") or 0) or None
            except ValueError:
                pass
            fallback = {
                401: "Phiên đăng nhập Dyna đã hết hạn",
                403: "Tài khoản chưa được phép dùng Trợ lý AI",
                429: "Bạn đang gửi yêu cầu quá nhanh",
                503: "Các dịch vụ AI đang tạm bận",
            }.get(response.status_code, "Trợ lý AI không xử lý được yêu cầu")
            raise AiAssistantError(
                _safe_detail(response, fallback),
                status_code=response.status_code,
                retry_after=retry_after,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise AiAssistantError("Máy chủ Dyna AI trả về dữ liệu không hợp lệ") from exc
        reply = str(payload.get("reply") or "").strip()
        if not reply:
            raise AiAssistantError("Trợ lý AI chưa tạo được câu trả lời")
        actions = [
            clean
            for action in (payload.get("actions") or [])
            if (clean := _normalize_action(action)) is not None
        ][:8]
        proposal_token = self._store_proposal(str(user["username"]), actions) if actions else ""
        return {
            "reply": reply,
            "actions": actions,
            "proposal_token": proposal_token,
            "proposal_expires_in": PROPOSAL_TTL_SECONDS if proposal_token else 0,
            "provider": str(payload.get("provider") or ""),
            "model": str(payload.get("model") or ""),
        }

    def _store_proposal(self, username: str, actions: list[dict[str, Any]]) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._remove_expired(now)
            self._proposals[token] = _Proposal(
                username=username.strip().lower(),
                actions=actions,
                expires_at=now + PROPOSAL_TTL_SECONDS,
            )
        return token

    def consume_proposal(self, token: str) -> list[dict[str, Any]]:
        user = auth_service.get_current_user()
        username = str((user or {}).get("username") or "").strip().lower()
        if not username:
            raise AiAssistantError("Cần đăng nhập Dyna để xác nhận hành động", status_code=401)
        now = time.monotonic()
        with self._lock:
            self._remove_expired(now)
            proposal = self._proposals.pop(str(token or "").strip(), None)
        if proposal is None or proposal.username != username:
            raise AiAssistantError(
                "Đề xuất đã hết hạn hoặc đã được sử dụng. Hãy hỏi Trợ lý AI lại.",
                status_code=409,
            )
        return proposal.actions

    def _remove_expired(self, now: float) -> None:
        expired = [token for token, item in self._proposals.items() if item.expires_at <= now]
        for token in expired:
            self._proposals.pop(token, None)
