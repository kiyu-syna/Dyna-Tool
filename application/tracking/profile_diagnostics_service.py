from __future__ import annotations

from typing import Any

import core.config as config
from application.tracking.sources import get_tracking_sources
from services.browser.browser_profile_service import browser_profile_label, browser_provider


def run_profile_diagnostics(profile_id: str | None = None) -> dict[str, Any]:
    from application.workflows.profile_worker import check_douyin_direct_download
    from application.publishing.upload_pipeline import enabled_platform_names
    from services.publishing.video_validation_service import FFprobeNotFoundError, resolve_ffprobe

    settings = config.load_settings()
    api_url = str(settings.get("API_URL") or config.API_URL)
    # The shared bot is configured and linked on the server, never locally.
    telegram_ready = False
    telegram_message = "Chưa kiểm tra được trạng thái Telegram trên máy chủ."
    try:
        from services.integrations.telegram_service import telegram_connection_status
        telegram = telegram_connection_status()
        telegram_ready = bool(telegram.get("configured") and telegram.get("linked"))
        telegram_message = "Đã liên kết với bot chung của Dyna." if telegram_ready else "Chưa liên kết Telegram với tài khoản Dyna."
    except Exception:
        pass
    try:
        ffprobe = {"ok": True, "path": resolve_ffprobe(), "message": "Sẵn sàng"}
    except FFprobeNotFoundError as exc:
        ffprobe = {"ok": False, "path": "", "message": str(exc)}

    profiles = config.load_profile_configs()
    if profile_id:
        normalized_id = str(profile_id)
        profiles = {
            normalized_id: profiles[normalized_id]
        } if normalized_id in profiles else {}

    rows = []
    for current_id, profile in sorted(
        profiles.items(), key=lambda item: str(item[0]).zfill(8)
    ):
        current_id = str(current_id)
        douyin = profile.get("douyin", {}) or {}
        gemlogin_id = str(douyin.get("gemlogin_profile_id") or current_id)
        sources = get_tracking_sources(profile)
        row = {
            "profile_id": current_id,
            "name": str(profile.get("name") or f"Profile {current_id}"),
            "gemlogin_profile_id": gemlogin_id,
            "browser_provider": browser_provider(profile),
            "browser_label": browser_profile_label(gemlogin_id, profile),
            "source_count": len(sources),
            "platforms": enabled_platform_names(profile),
            "ok": False,
            "message": "",
        }
        if not sources:
            row["message"] = "Không có nguồn theo dõi nào đang bật."
            rows.append(row)
            continue
        try:
            result = check_douyin_direct_download(
                gemlogin_id,
                api_url=api_url,
                profile_config=profile,
            )
            row["ok"] = bool(result.get("ok"))
            row["message"] = str(
                result.get("message")
                or ("Tải trực tiếp sẵn sàng." if row["ok"] else "Không kiểm tra được tải trực tiếp.")
            )
        except Exception as exc:
            row["message"] = str(exc)
        rows.append(row)

    return {
        "api_url": api_url,
        "telegram": {
            "ok": telegram_ready,
            "message": telegram_message,
        },
        "ffprobe": ffprobe,
        "profiles": rows,
    }
