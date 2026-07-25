from __future__ import annotations

import copy
import os

import core.config as config
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
from profile_automation.pipeline.upload_pipeline import (
    UploadPipeline,
    all_enabled_uploads_succeeded,
    enabled_platform_names,
)
from profile_automation.pipeline.video_job_store import TERMINAL_STATUSES, VideoJobStore
from profile_automation.tracking_sources import tracking_source_label
from profile_automation.watchers.douyin_profile_monitor import DouyinProfileMonitor, DouyinVideo
from profile_automation.watchers.tiktok_profile_monitor import TikTokProfileMonitor
from services.browser.browser_profile_service import reuse_browser_connections
from services.integrations.telegram_service import (
    send_error_notification,
    send_video_upload_summary_notification,
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

        self.save_dir = profile_config.get("save_dir")
        if not self.save_dir:
            self.save_dir = os.path.join(config.BASE_DIR, "Downloads", f"profile_{self.profile_id}")

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

    @reuse_browser_connections
    def process_video(
        self,
        video: DouyinVideo,
        source: dict,
        monitor: DouyinProfileMonitor,
        stop_event=None,
    ) -> dict:
        self.register_video(video, source)
        job = self.job_store.get_job(self.profile_id, video.aweme_id) or {}
        source_platform = str(source.get("platform") or "douyin").strip().casefold()
        target_platforms = enabled_platform_names(self.profile, source_platform)
        if job.get("status") in TERMINAL_STATUSES:
            if job.get("status") == "completed":
                monitor.mark_processed(video)
            elif job.get("status") in {"cancelled", "ignored"}:
                monitor.mark_ignored(video)
            return {"status": job.get("status"), "results": {}}

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
            return {"status": "ignored", "results": {}, "error": reason}

        if not self.job_store.claim(self.profile_id, video.aweme_id):
            logger.info(
                "[Profile %s] Video %s đang được một luồng khác xử lý.",
                self.profile_id,
                video.aweme_id,
            )
            return {"status": "busy", "results": {}}

        video_path = ""
        try:
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
                self.job_store.set_caption(self.profile_id, video.aweme_id, selected_caption)

            profile_for_upload = copy.deepcopy(self.profile)
            profile_for_upload["_runtime_caption"] = selected_caption
            profile_for_upload["_runtime_use_original_desc"] = use_source_original_caption(self.profile, source_platform)

            job = self.job_store.get_job(self.profile_id, video.aweme_id) or {}
            saved_path = str(job.get("download_path") or "")
            deterministic_path = (
                get_tiktok_tracking_download_path(self.profile_id, video.aweme_id)
                if source_platform == "tiktok"
                else get_tracking_download_path(self.profile_id, video.aweme_id)
            )
            for candidate in (saved_path, deterministic_path):
                if candidate and os.path.isfile(candidate):
                    video_path = candidate
                    break

            if not video_path:
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
                    return {"status": "failed_download", "results": {}, "error": error}

            self.job_store.set_download_path(self.profile_id, video.aweme_id, video_path)
            try:
                media_info = validate_video_file(video_path, require_audio=True)
                self.job_store.set_media_info(self.profile_id, video.aweme_id, media_info)
                logger.info(
                    "[Profile %s] Video %s hợp lệ: %sx%s, %.1fs, audio=%s, %s byte.",
                    self.profile_id,
                    video.aweme_id,
                    media_info["width"],
                    media_info["height"],
                    media_info["duration_seconds"],
                    media_info["has_audio"],
                    media_info["file_size"],
                )
            except (FFprobeNotFoundError, InvalidVideoFileError) as exc:
                error = f"Kiểm tra file video thất bại: {exc}"
                if isinstance(exc, InvalidVideoFileError):
                    if discard_invalid_download(video_path):
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
                return {"status": "failed_download", "results": {}, "error": error}

            successful_platforms = self.job_store.successful_platforms(
                self.profile_id, video.aweme_id
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
                current_job = self.job_store.get_job(self.profile_id, video.aweme_id) or {}
                return current_job.get("status") == "cancelled"

            results = self.pipeline.run(
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
                    logger.debug("Không thể xóa file video đã hoàn tất %s.", video_path)
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
        except Exception as exc:
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
        finally:
            self.job_store.release(self.profile_id, video.aweme_id)
