"""Desktop relay for the server-owned shared Telegram bot.

No Telegram token is read, stored, or used on the desktop.  This module only
uses the authenticated Dyna API and therefore remains safe in packaged apps.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import requests

from services.account import auth_service, license_service
from services.browser.gemlogin_browser_service import classify_automation_error

ERROR_NOTIFICATION_DEDUPE_SECONDS = 10 * 60
_ERROR_NOTIFICATION_TIMES: dict[tuple[str, str, str], float] = {}
logger = logging.getLogger(__name__)


def _headers() -> dict[str, str] | None:
    user = auth_service.get_current_user()
    token = str((user or {}).get("token") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else None


def _request(method: str, path: str, *, body: dict[str, Any] | None = None, timeout: float = 12) -> dict[str, Any] | None:
    headers = _headers()
    if not headers:
        return None
    try:
        response = requests.request(method, f"{license_service.get_api_base_url()}{path}", headers=headers, json=body, timeout=timeout)
        if response.status_code >= 400:
            logger.warning("Telegram server relay rejected %s: %s", path, response.status_code)
            return None
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Telegram server relay unavailable: %s", exc)
        return None


def telegram_connection_status() -> dict[str, Any]:
    return _request("GET", "/api/telegram/status") or {"configured": False, "linked": False}


def create_telegram_link_code() -> dict[str, Any]:
    result = _request("POST", "/api/telegram/link-code")
    if result is None:
        raise RuntimeError("Không kết nối được máy chủ Telegram của Dyna")
    return result


def unlink_telegram() -> bool:
    return bool(_request("DELETE", "/api/telegram/link"))


def create_caption_request(
    *,
    profile_id: str,
    profile_name: str,
    source_label: str,
    video_id: str,
    description: str,
    default_caption: str,
    pin_message: bool = False,
) -> str:
    result = _request(
        "POST",
        "/api/telegram/captions",
        body={
            "profile_id": str(profile_id or ""),
            "profile_name": str(profile_name or ""),
            "source_label": str(source_label or ""),
            "video_id": str(video_id or ""),
            "description": str(description or "")[:1200],
            "default_caption": str(default_caption or "")[:10000],
            "pin_message": bool(pin_message),
        },
    )
    if not result:
        raise RuntimeError("Không thể tạo yêu cầu caption trên Telegram.")
    if not result.get("available"):
        raise RuntimeError(
            str(result.get("reason") or "Telegram chưa được liên kết với Dyna.")
        )
    request_id = str(result.get("request_id") or "").strip()
    if not request_id:
        raise RuntimeError("Máy chủ Telegram không trả về mã yêu cầu caption.")
    if pin_message and not result.get("pinned"):
        logger.warning(
            "Không thể ghim yêu cầu caption Telegram cho Profile %s: %s",
            profile_id,
            result.get("pin_error") or "bot không có quyền ghim tin nhắn",
        )
    return request_id


def wait_for_caption(
    request_id: str,
    *,
    cancel_event=None,
    poll_interval_seconds: float = 3,
) -> str:
    """Wait without a deadline until the user replies to the Telegram prompt."""
    request_id = str(request_id or "").strip()
    if not request_id:
        raise ValueError("Thiếu mã yêu cầu caption Telegram.")

    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Profile đã dừng khi đang chờ caption Telegram.")

        result = _request(
            "GET",
            f"/api/telegram/captions/{request_id}",
            timeout=12,
        )
        if result:
            status = str(result.get("status") or "pending").strip().casefold()
            if status == "selected":
                return str(result.get("caption") or "")
            if status in {"missing", "expired", "skipped"}:
                raise RuntimeError(
                    "Yêu cầu caption Telegram không còn khả dụng; hãy thử lại video."
                )

        wait_seconds = max(0.2, float(poll_interval_seconds))
        if cancel_event is not None:
            if cancel_event.wait(wait_seconds):
                raise InterruptedError(
                    "Profile đã dừng khi đang chờ caption Telegram."
                )
        else:
            time.sleep(wait_seconds)


def _notify(text: str, *, cancel_job: dict[str, str] | None = None) -> None:
    body: dict[str, Any] = {"text": text[:4000]}
    if cancel_job:
        body["cancel_job"] = cancel_job
    _request("POST", "/api/telegram/notifications", body=body)


def claim_remote_action() -> dict[str, Any] | None:
    """Claim one Telegram-approved action for this authenticated desktop."""
    result = _request("POST", "/api/telegram/remote-actions/claim", timeout=10)
    action = (result or {}).get("action")
    return action if isinstance(action, dict) else None


def complete_remote_action(request_id: str, results: list[dict[str, Any]]) -> bool:
    result = _request("POST", "/api/telegram/remote-actions/complete", body={
        "request_id": str(request_id),
        "results": results,
    }, timeout=12)
    return bool((result or {}).get("accepted"))


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S %d/%m/%Y")


def _profile(profile_id: str, profile_name: str = "") -> str:
    return f"{profile_id} - {profile_name}" if profile_name else str(profile_id)


def send_new_video_notification(profile_id: str, profile_name: str, source_label: str, video: Any, platforms) -> None:
    labels = {"tiktok": "TikTok", "facebook": "Facebook Reels", "youtube": "YouTube Shorts"}
    names = ", ".join(labels.get(str(item).lower(), str(item).title()) for item in (platforms or ())) or "Không có"
    _notify(
        f"PHÁT HIỆN VIDEO MỚI\n\nHồ sơ: {_profile(profile_id, profile_name)}\nNguồn: {source_label or '-'}\n"
        f"Sẽ đăng lên: {names}\nLink: {getattr(video, 'share_url', '')}\nThời gian: {_timestamp()}",
        cancel_job={"profile_id": str(profile_id), "video_id": str(getattr(video, "aweme_id", "") or "")},
    )


def send_video_upload_summary_notification(profile_id: str, profile_name: str, source_label: str, video: Any, platforms, results: dict, platform_states: dict | None = None) -> None:
    states = platform_states or {}
    labels = {"tiktok": "TikTok", "facebook": "Facebook Reels", "youtube": "YouTube Shorts"}
    lines = []
    success_count = 0
    for platform in platforms or ():
        ok = results.get(platform) is True or str((states.get(platform) or {}).get("status")) == "success"
        state = states.get(platform) or {}
        if ok:
            success_count += 1
            outcome = "Thành công"
        else:
            error = str(state.get("last_error") or "") or str(results.get(platform) or "")
            outcome = f"Không thành công{f' - {error}' if error else ''}"
        lines.append(f"- {labels.get(str(platform).lower(), str(platform).title())}: {outcome}")
    total = len(platforms or ())
    _notify(
        f"KẾT QUẢ XỬ LÝ VIDEO - ĐĂNG THÀNH CÔNG {success_count}/{total} NỀN TẢNG\n\n"
        f"Hồ sơ: {_profile(profile_id, profile_name)}\nNguồn: {source_label or '-'}\n"
        f"Link: {getattr(video, 'share_url', '')}\n\n" + "\n".join(lines) + f"\n\nThời gian: {_timestamp()}"
    )


def send_error_notification(error_message: str, profile_id: str | None = None, video_id: str | None = None) -> None:
    category = classify_automation_error(error_message)
    key = (str(profile_id or ""), str(video_id or ""), category)
    now = time.monotonic()
    if now - _ERROR_NOTIFICATION_TIMES.get(key, 0) < ERROR_NOTIFICATION_DEDUPE_SECONDS:
        return
    _ERROR_NOTIFICATION_TIMES[key] = now
    detail = "\n".join(part for part in [f"Hồ sơ: {profile_id}" if profile_id else "", f"Video: {video_id}" if video_id else "", f"Lỗi: {error_message}", f"Thời gian: {_timestamp()}"] if part)
    _notify(f"CÓ LỖI XẢY RA\n\n{detail}")


def send_screenshot_notification(image_path, caption):
    _notify(f"HỆ THỐNG CẦN CHÚ Ý\n\n{caption}\n\nẢnh chụp đã được lưu cục bộ trong Dyna.")


def send_diagnostic_notification(record: dict) -> None:
    _notify(f"CHẨN ĐOÁN TỰ ĐỘNG\n\nHồ sơ: {record.get('profile_id') or '-'}\nVideo: {record.get('video_id') or '-'}\nNền tảng: {record.get('platform') or '-'}\nLỗi: {record.get('error') or '-'}")
