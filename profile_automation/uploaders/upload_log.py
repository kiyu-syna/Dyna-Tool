from __future__ import annotations

from core.utils import logger


SECTION_RULE = "-" * 20


def log_upload_section(
    platform: str,
    profile_id: str,
    video_id: str,
    phase: str,
    *,
    status: str = "",
    level: str = "info",
) -> None:
    """Write one highly visible boundary around a platform upload run."""
    heading = f"{phase.strip().upper()} ĐĂNG {platform.strip().upper()}"
    if status:
        heading += f" | {status.strip().upper()}"
    getattr(logger, level)(
        "%s %s | [Profile %s] | VIDEO %s %s",
        SECTION_RULE,
        heading,
        profile_id,
        video_id,
        SECTION_RULE,
    )
