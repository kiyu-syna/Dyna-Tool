from __future__ import annotations

import copy
import os
from typing import Any

import core.config as config
from application.publishing.upload_pipeline import (
    UploadPipeline,
    all_enabled_uploads_succeeded,
    enabled_platform_names,
)
from application.tracking.sources import tracking_source_label
from application.workflows.video_job_store import TERMINAL_STATUSES, VideoJobStore
from core.utils import logger
from profile_automation.pipeline.downloads.captions import (
    get_tiktok_tracking_download_path,
    get_tracking_download_path,
    resolve_runtime_caption,
    use_source_original_caption,
)
from profile_automation.pipeline.downloads.douyin import (
    check_douyin_direct_download,
    download_douyin_video_direct,
)
from profile_automation.pipeline.downloads.tiktok import (
    download_tiktok_video_direct,
)
from profile_automation.pipeline.job_recovery import (
    cleanup_completed_download,
    discard_invalid_download,
    next_status_after_cancellation,
)
from profile_automation.watchers.douyin_profile_monitor import DouyinProfileMonitor
from profile_automation.watchers.douyin_video import DouyinVideo
from profile_automation.watchers.tiktok_profile_monitor import TikTokProfileMonitor
from services.browser.browser_profile_service import reuse_browser_connections
from services.integrations.telegram_service import (
    create_caption_request,
    send_error_notification,
    send_video_upload_summary_notification,
    wait_for_caption,
)
from services.publishing.video_validation_service import (
    FFprobeNotFoundError,
    InvalidVideoFileError,
    validate_video_file,
)
from services.runtime.workload_coordinator import WorkloadCancelled, priority_for_job


class ProfileWorker:
    def __init__(self, profile_config: dict):
        self.profile = profile_config
        self.profile_id = str(profile_config.get("id"))
        self.name = profile_config.get("name", "Unnamed Profile")

        douyin_cfg = profile_config.get("douyin", {})
        self.gemlogin_id = str(douyin_cfg.get("gemlogin_profile_id") or self.profile_id)
        filters = profile_config.get("filters", {})
        self.min_likes = filters.get("min_likes", 0)
        self.max_duration = filters.get("max_duration_seconds", 300)

        self.pipeline = UploadPipeline()
        self.job_store = VideoJobStore()
        self.save_dir = profile_config.get("save_dir") or os.path.join(
            config.BASE_DIR,
            "Downloads",
            f"profile_{self.profile_id}",
        )

    def _create_monitor(self, source: dict):
        if source.get("platform") == "tiktok":
            return TikTokProfileMonitor(
                profile_id=self.profile_id,
                unique_id=source["unique_id"],
                sec_uid=source.get("sec_uid", ""),
                gemlogin_profile_id=self.gemlogin_id,
                api_url=config.API_URL,
                min_likes=self.min_likes,
                max_duration_sec=self.max_duration,
                source_key=source["source_key"],
                profile_config=self.profile,
            )
        return DouyinProfileMonitor(
            profile_id=self.profile_id,
            sec_uid=source["target_sec_uid"],
            gemlogin_profile_id=self.gemlogin_id,
            api_url=config.API_URL,
            min_likes=self.min_likes,
            max_duration_sec=self.max_duration,
            source_key=source["source_key"],
            profile_config=self.profile,
        )

    @staticmethod
    def _source_label(source: dict) -> str:
        return tracking_source_label(source)

    def get_pending_videos(self, source: dict) -> list:
        return self.job_store.list_pending_videos(
            self.profile_id,
            source_key=source.get("source_key", ""),
            source_platform=source.get("platform", "douyin"),
        )

    def register_video(self, video: DouyinVideo, source: dict) -> dict:
        return self.job_store.ensure_job(
            self.profile_id,
            video,
            self.profile,
            source_key=source.get("source_key", ""),
            source_label=self._source_label(source),
            source_platform=source.get("platform", "douyin"),
        )

    def mark_ignored_job(
        self,
        video: DouyinVideo,
        source: dict,
        monitor: DouyinProfileMonitor,
        reason: str,
    ) -> None:
        self.register_video(video, source)
        self.job_store.set_status(self.profile_id, video.aweme_id, "ignored", error=reason)
        monitor.mark_ignored(video)

    def claim_job(
        self,
        video: DouyinVideo,
        source: dict,
        monitor: DouyinProfileMonitor,
    ) -> tuple[str, list[str], dict | None]:
        self.register_video(video, source)
        job = self.job_store.get_job(self.profile_id, video.aweme_id) or {}
        source_platform = str(source.get("platform") or "douyin").strip().casefold()
        target_platforms = enabled_platform_names(self.profile, source_platform)

        if job.get("status") in TERMINAL_STATUSES:
            if job.get("status") == "completed":
                monitor.mark_processed(video)
            elif job.get("status") in {"cancelled", "ignored"}:
                monitor.mark_ignored(video)
            return source_platform, target_platforms, {
                "status": job.get("status"),
                "results": {},
            }

        if str(job.get("status") or "").startswith("failed"):
            logger.warning(
                "[Profile %s] Không tự đăng lại video %s đang lỗi; "
                "chỉ nút Thử lại trên hàng đợi mới được phép chạy lại.",
                self.profile_id,
                video.aweme_id,
            )
            return source_platform, target_platforms, {
                "status": job.get("status"),
                "results": {},
                "error": str(job.get("last_error") or ""),
            }

        if not target_platforms:
            reason = (
                "Không có nền tảng đích hợp lệ; nền tảng trùng với nguồn video "
                "luôn bị bỏ qua."
            )
            self.job_store.set_status(
                self.profile_id,
                video.aweme_id,
                "ignored",
                error=reason,
            )
            monitor.mark_ignored(video)
            return source_platform, target_platforms, {
                "status": "ignored",
                "results": {},
                "error": reason,
            }

        if not self.job_store.claim(self.profile_id, video.aweme_id):
            logger.info(
                "[Profile %s] Video %s đang được một luồng khác xử lý.",
                self.profile_id,
                video.aweme_id,
            )
            return source_platform, target_platforms, {
                "status": "busy",
                "results": {},
            }

        return source_platform, target_platforms, None

    def resolve_caption(
        self,
        video: DouyinVideo,
        source: dict,
        source_platform: str,
        stop_event=None,
    ) -> tuple[dict, int]:
        job = self.job_store.get_job(self.profile_id, video.aweme_id) or {}
        work_priority = priority_for_job(job, self.profile)
        if job.get("caption_resolved"):
            selected_caption = str(job.get("caption") or "")
        else:
            selected_caption = resolve_runtime_caption(
                self.profile,
                self.profile_id,
                video,
                source_label=self._source_label(source),
                source_platform=source_platform,
            )
            caption_options = self.profile.get("caption_options") or {}
            if caption_options.get("telegram_use_custom_caption") is True:
                request_id = str(job.get("caption_request_id") or "").strip()
                if not request_id:
                    request_id = create_caption_request(
                        profile_id=self.profile_id,
                        profile_name=self.name,
                        source_label=self._source_label(source),
                        video_id=video.aweme_id,
                        description=str(getattr(video, "desc", "") or ""),
                        default_caption=selected_caption,
                        pin_message=caption_options.get(
                            "telegram_pin_caption_message"
                        )
                        is True,
                    )
                    self.job_store.set_caption_request(
                        self.profile_id,
                        video.aweme_id,
                        request_id,
                    )
                elif job.get("status") != "waiting_caption":
                    self.job_store.set_status(
                        self.profile_id,
                        video.aweme_id,
                        "waiting_caption",
                    )
                selected_caption = wait_for_caption(
                    request_id,
                    cancel_event=stop_event,
                )
            self.job_store.set_caption(
                self.profile_id,
                video.aweme_id,
                selected_caption,
            )

        profile_for_upload = copy.deepcopy(self.profile)
        profile_for_upload["_runtime_caption"] = selected_caption
        profile_for_upload["_runtime_use_original_desc"] = use_source_original_caption(
            self.profile,
            source_platform,
        )
        return profile_for_upload, work_priority

    def download_video(
        self,
        video: DouyinVideo,
        source_platform: str,
        work_priority: int,
        stop_event=None,
    ) -> tuple[str, dict | None]:
        job = self.job_store.get_job(self.profile_id, video.aweme_id) or {}
        saved_path = str(job.get("download_path") or "")
        deterministic_path = (
            get_tiktok_tracking_download_path(self.profile_id, video.aweme_id)
            if source_platform == "tiktok"
            else get_tracking_download_path(self.profile_id, video.aweme_id)
        )
        for candidate in (saved_path, deterministic_path):
            if candidate and os.path.isfile(candidate):
                self.job_store.set_download_path(
                    self.profile_id,
                    video.aweme_id,
                    candidate,
                )
                return candidate, None

        self.job_store.set_status(
            self.profile_id,
            video.aweme_id,
            "downloading",
            increment_attempt="download",
        )
        downloader = (
            download_tiktok_video_direct
            if source_platform == "tiktok"
            else download_douyin_video_direct
        )
        video_path = downloader(
            video,
            self.profile_id,
            self.gemlogin_id,
            priority=work_priority,
            cancel_event=stop_event,
            profile_config=self.profile,
        ) or ""
        if not video_path or not os.path.isfile(video_path):
            error = f"Không thể tải trực tiếp video {source_platform.title()}."
            self.job_store.set_status(
                self.profile_id,
                video.aweme_id,
                "failed_download",
                error=error,
            )
            return "", {
                "status": "failed_download",
                "results": {},
                "error": error,
            }

        self.job_store.set_download_path(self.profile_id, video.aweme_id, video_path)
        return video_path, None

    def validate_video(
        self,
        video: DouyinVideo,
        video_path: str,
    ) -> dict | None:
        try:
            media_info = validate_video_file(video_path, require_audio=True)
            self.job_store.set_media_info(
                self.profile_id,
                video.aweme_id,
                media_info,
            )
            logger.info(
                "[Profile %s] Video %s hợp lệ: %sx%s, %.1f giây, âm thanh=%s, %s byte.",
                self.profile_id,
                video.aweme_id,
                media_info["width"],
                media_info["height"],
                media_info["duration_seconds"],
                "có" if media_info["has_audio"] else "không",
                media_info["file_size"],
            )
            return None
        except (FFprobeNotFoundError, InvalidVideoFileError) as exc:
            error = f"Kiểm tra tệp video thất bại: {exc}"
            if isinstance(exc, InvalidVideoFileError) and discard_invalid_download(
                video_path
            ):
                self.job_store.set_download_path(self.profile_id, video.aweme_id, "")
            self.job_store.set_status(
                self.profile_id,
                video.aweme_id,
                "failed_download",
                error=error,
            )
            send_error_notification(
                error,
                profile_id=self.profile_id,
                video_id=video.aweme_id,
            )
            return {
                "status": "failed_download",
                "results": {},
                "error": error,
            }

    def upload_platforms(
        self,
        video: DouyinVideo,
        video_path: str,
        profile_for_upload: dict,
        source_platform: str,
        work_priority: int,
        stop_event=None,
    ) -> dict[str, bool]:
        successful_platforms = self.job_store.successful_platforms(
            self.profile_id,
            video.aweme_id,
        )
        self.job_store.set_status(self.profile_id, video.aweme_id, "uploading")

        def record_platform_status(platform: str, status: str, error: str) -> None:
            self.job_store.set_platform_status(
                self.profile_id,
                video.aweme_id,
                platform,
                status,
                error=error,
            )

        def upload_cancelled() -> bool:
            current_job = self.job_store.get_job(
                self.profile_id,
                video.aweme_id,
            ) or {}
            return current_job.get("status") == "cancelled"

        return self.pipeline.run(
            video_path,
            video,
            profile_for_upload,
            successful_platforms=successful_platforms,
            on_platform_status=record_platform_status,
            priority=work_priority,
            cancel_event=stop_event,
            should_cancel=upload_cancelled,
            source_platform=source_platform,
        )

    def finalize_job(
        self,
        video: DouyinVideo,
        source: dict,
        monitor: DouyinProfileMonitor,
        video_path: str,
        profile_for_upload: dict,
        source_platform: str,
        results: dict[str, bool],
    ) -> dict:
        finished_job = self.job_store.get_job(self.profile_id, video.aweme_id) or {}
        if finished_job.get("status") == "cancelled":
            monitor.mark_ignored(video)
            return {"status": "cancelled", "results": results}

        send_video_upload_summary_notification(
            self.profile_id,
            self.name,
            self._source_label(source),
            video,
            enabled_platform_names(profile_for_upload, source_platform),
            results,
            platform_states=finished_job.get("platforms", {}),
        )
        if all_enabled_uploads_succeeded(
            results,
            profile_for_upload,
            source_platform,
        ):
            self.job_store.set_status(self.profile_id, video.aweme_id, "completed")
            monitor.mark_processed(video)
            if cleanup_completed_download(video_path):
                self.job_store.set_download_path(self.profile_id, video.aweme_id, "")
            else:
                logger.debug("Không thể xóa tệp video đã hoàn tất %s.", video_path)
            return {"status": "completed", "results": results}

        failed_platforms = [
            platform
            for platform in enabled_platform_names(
                profile_for_upload,
                source_platform,
            )
            if not results.get(platform, False)
        ]
        error = "Đăng thất bại: " + ", ".join(failed_platforms)
        self.job_store.set_status(
            self.profile_id,
            video.aweme_id,
            "failed_upload",
            error=error,
        )
        send_error_notification(
            error,
            profile_id=self.profile_id,
            video_id=video.aweme_id,
        )
        return {"status": "failed_upload", "results": results, "error": error}

    def _handle_processing_failure(self, video: DouyinVideo, exc: Exception) -> dict:
        error = str(exc)
        logger.exception(
            "[Profile %s] Lỗi xử lý video %s: %s",
            self.profile_id,
            video.aweme_id,
            exc,
        )
        try:
            self.job_store.set_status(
                self.profile_id,
                video.aweme_id,
                "failed",
                error=error,
            )
        except Exception:
            pass
        send_error_notification(
            f"Lỗi xử lý video: {error}",
            profile_id=self.profile_id,
            video_id=video.aweme_id,
        )
        return {"status": "failed", "results": {}, "error": error}

    @reuse_browser_connections
    def process_video(
        self,
        video: DouyinVideo,
        source: dict,
        monitor: DouyinProfileMonitor,
        stop_event=None,
    ) -> dict[str, Any]:
        source_platform, _target_platforms, early_result = self.claim_job(
            video,
            source,
            monitor,
        )
        if early_result is not None:
            return early_result

        try:
            profile_for_upload, work_priority = self.resolve_caption(
                video,
                source,
                source_platform,
                stop_event,
            )
            video_path, download_error = self.download_video(
                video,
                source_platform,
                work_priority,
                stop_event,
            )
            if download_error is not None:
                return download_error

            validation_error = self.validate_video(video, video_path)
            if validation_error is not None:
                return validation_error

            results = self.upload_platforms(
                video,
                video_path,
                profile_for_upload,
                source_platform,
                work_priority,
                stop_event,
            )
            return self.finalize_job(
                video,
                source,
                monitor,
                video_path,
                profile_for_upload,
                source_platform,
                results,
            )
        except WorkloadCancelled:
            current = self.job_store.get_job(self.profile_id, video.aweme_id) or {}
            next_status = next_status_after_cancellation(current)
            self.job_store.set_status(self.profile_id, video.aweme_id, next_status)
            logger.info(
                "[Profile %s] Dừng chờ tài nguyên cho video %s.",
                self.profile_id,
                video.aweme_id,
            )
            return {"status": "stopped", "results": {}}
        except InterruptedError:
            logger.info(
                "[Profile %s] Dừng chờ mô tả Telegram cho video %s.",
                self.profile_id,
                video.aweme_id,
            )
            return {"status": "stopped", "results": {}}
        except Exception as exc:
            return self._handle_processing_failure(video, exc)
        finally:
            self.job_store.release(self.profile_id, video.aweme_id)
