from __future__ import annotations

import os
from typing import Optional

from core.utils import logger
from profile_automation.watchers.douyin_profile_monitor import DouyinVideo


def get_tracking_download_path(profile_id: str, video_id: str) -> str:
    save_dir = os.path.join(
        os.path.expanduser("~"),
        "Videos",
        "Tracking Douyin",
        f"Profile {profile_id}",
    )
    filename = f"[Profile {profile_id}]_{video_id}.mp4"
    return os.path.normpath(os.path.join(save_dir, filename))


def get_tiktok_tracking_download_path(profile_id: str, video_id: str) -> str:
    save_dir = os.path.join(
        os.path.expanduser("~"),
        "Videos",
        "Tracking TikTok",
        f"Profile {profile_id}",
    )
    filename = f"[Profile {profile_id}]_{video_id}.mp4"
    return os.path.normpath(os.path.join(save_dir, filename))


def use_source_original_caption(profile: dict, source_platform: str) -> bool:
    key = f"{str(source_platform or 'douyin').strip().casefold()}_use_original_desc"
    return bool((profile.get("caption_options") or {}).get(key, False))


def resolve_runtime_caption(
    profile: dict,
    profile_id: str,
    video: DouyinVideo,
    source_label: str = "",
    source_platform: str = "douyin",
) -> Optional[str]:
    if use_source_original_caption(profile, source_platform):
        return str(getattr(video, "desc", "") or "")
    caption = str(profile.get("default_caption") or "").strip()
    if not caption:
        caption = str(getattr(video, "desc", "") or "").strip()
    logger.info(
        "[Profile %s] Dùng mô tả mặc định cho video %s.",
        profile_id,
        video.aweme_id,
    )
    return caption
