from __future__ import annotations

from typing import Any

import core.config as config
from profile_automation.douyin_sources import get_douyin_sources


def run_profile_diagnostics(profile_id: str | None = None) -> dict[str, Any]:
    from profile_automation.pipeline.profile_worker import check_douyin_direct_download
    from profile_automation.pipeline.upload_pipeline import enabled_platform_names
    from services.video_validation_service import FFprobeNotFoundError, resolve_ffprobe

    settings = config.load_settings()
    api_url = str(settings.get("API_URL") or config.API_URL)
    telegram_ready = bool(
        settings.get("TELEGRAM_BOT_TOKEN") and settings.get("TELEGRAM_CHAT_ID")
    )
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
        sources = get_douyin_sources(profile)
        row = {
            "profile_id": current_id,
            "name": str(profile.get("name") or f"Profile {current_id}"),
            "gemlogin_profile_id": gemlogin_id,
            "source_count": len(sources),
            "platforms": enabled_platform_names(profile),
            "ok": False,
            "message": "",
        }
        if not sources:
            row["message"] = "Không có nguồn Douyin nào đang bật."
            rows.append(row)
            continue
        try:
            result = check_douyin_direct_download(gemlogin_id, api_url=api_url)
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
            "message": "Đã cấu hình token và Chat ID." if telegram_ready else "Chưa cấu hình đủ token hoặc Chat ID.",
        },
        "ffprobe": ffprobe,
        "profiles": rows,
    }
