from __future__ import annotations

from contextlib import ExitStack
from typing import Optional

import core.config as config
from core.utils import console, logger
from profile_automation.pipeline.downloads.captions import get_tiktok_tracking_download_path
from profile_automation.pipeline.downloads.douyin import _close_page_quietly, _download_douyin_candidates
from profile_automation.watchers.tiktok_profile_monitor import TikTokVideo
from services.browser.browser_profile_service import (
    connected_browser_profile as connected_gemlogin_profile,
    create_background_page,
    run_with_browser_recovery as run_with_gemlogin_recovery,
)
from services.integrations.diagnostic_artifact_service import attach_response_trace, record_browser_diagnostic
from services.integrations.telegram_service import send_diagnostic_notification, send_error_notification
from services.runtime.workload_coordinator import WorkloadCancelled, workload_slot

def _download_tiktok_video_direct_once(
    video: TikTokVideo,
    profile_id: str,
    gemlogin_profile_id: str,
    api_url: str = config.API_URL,
    profile_config: Optional[dict] = None,
) -> Optional[str]:
    """Download TikTok directly from signed URLs captured in item_list."""
    save_path = get_tiktok_tracking_download_path(profile_id, video.aweme_id)
    page = None
    response_trace: dict = {}
    try:
        candidates = list(getattr(video, "download_urls", []) or [])
        primary_url = str(getattr(video, "download_url", "") or "").strip()
        if primary_url:
            candidates.insert(0, primary_url)
        candidates = list(
            dict.fromkeys(
                str(candidate).strip()
                for candidate in candidates
                if str(candidate or "").strip().startswith(("http://", "https://"))
            )
        )
        if not candidates:
            raise RuntimeError(
                f"item_list TikTok không chứa URL tải trực tiếp cho video {video.aweme_id}."
            )

        with connected_gemlogin_profile(
            gemlogin_profile_id,
            api_url,
            profile_config=profile_config,
            close_profile_on_exit=True,
        ) as browser, ExitStack() as page_cleanup:
            if not browser.contexts:
                raise RuntimeError("Browser không có context để tải trực tiếp video TikTok.")

            context = browser.contexts[0]
            page = create_background_page(browser, context)
            page_cleanup.callback(_close_page_quietly, page)
            response_trace = attach_response_trace(page)
            result = {
                "status": "success",
                "source": "tiktok_profile_item_list",
                "aweme_id": video.aweme_id,
                "video_id": video.aweme_id,
                "download_url": candidates[0],
                "download_urls": candidates,
                "referer": video.share_url or "https://www.tiktok.com/",
                "headers_required": False,
                "headers": {},
            }
            console.print(
                f"[cyan]   → Đang tải trực tiếp video TikTok {video.aweme_id} "
                f"từ item_list ({len(candidates)} URL dự phòng)...[/]"
            )
            downloaded_path = _download_douyin_candidates(
                context,
                page,
                result,
                save_path,
                platform_label="TikTok",
            )
            console.print(f"[green]   → Đã tải xong video TikTok: {downloaded_path}[/]")
            return downloaded_path
    except Exception as exc:
        diagnostic_record = record_browser_diagnostic(
            page=page,
            profile_id=profile_id,
            video_id=str(video.aweme_id),
            platform="tiktok",
            error=exc,
            url=str(getattr(video, "share_url", "") or ""),
            last_response=response_trace,
            send_telegram=False,
        )
        try:
            setattr(exc, "diagnostic_record", diagnostic_record)
        except Exception:
            pass
        logger.exception(
            "[Profile %s] Lỗi tải trực tiếp video TikTok %s: %s",
            profile_id,
            video.aweme_id,
            exc,
        )
        raise
    finally:
        if page is not None:
            _close_page_quietly(page)


def download_tiktok_video_direct(
    video: TikTokVideo,
    profile_id: str,
    gemlogin_profile_id: str,
    api_url: str = config.API_URL,
    *,
    priority: int = 100,
    cancel_event=None,
    profile_config: Optional[dict] = None,
) -> Optional[str]:
    """Download TikTok without using the downloader extension."""
    try:
        with workload_slot(
            "download",
            profile_id=profile_id,
            video_id=video.aweme_id,
            priority=priority,
            cancel_event=cancel_event,
        ):
            return run_with_gemlogin_recovery(
                lambda: _download_tiktok_video_direct_once(
                    video,
                    profile_id,
                    gemlogin_profile_id,
                    api_url,
                    profile_config,
                ),
                gemlogin_profile_id,
                api_url,
                operation_name=f"Tải trực tiếp video TikTok {video.aweme_id}",
                attempts=2,
                profile_config=profile_config,
            )
    except WorkloadCancelled:
        raise
    except Exception as exc:
        send_error_notification(
            f"Lỗi tải trực tiếp video TikTok: {exc}",
            profile_id=profile_id,
            video_id=video.aweme_id,
        )
        diagnostic_record = getattr(exc, "diagnostic_record", None)
        if isinstance(diagnostic_record, dict):
            send_diagnostic_notification(diagnostic_record)
        return None

