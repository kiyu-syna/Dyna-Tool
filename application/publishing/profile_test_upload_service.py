from __future__ import annotations

import copy
import os
import threading
from datetime import datetime
from typing import Any

import core.config as config
from core.utils import logger
from application.publishing.upload_pipeline import enabled_platform_names
from application.tracking.sources import get_tracking_sources
from application.tracking.profile_management_service import ProfileManagementService
from services.runtime.workload_coordinator import workload_slot


class ProfileTestUploadService:
    """Runs the first-video upload test without any UI ownership."""

    def __init__(self, profiles: ProfileManagementService | None = None):
        self.profiles = profiles or ProfileManagementService()
        self._lock = threading.RLock()
        self._states: dict[str, dict[str, Any]] = {}

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._states.items()}

    def _update(self, profile_id: str, **changes: Any) -> None:
        with self._lock:
            current = self._states.setdefault(profile_id, {"profile_id": profile_id})
            current.update(changes)
            current["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def start(self, profile_id: str) -> dict[str, Any]:
        profile_id = self.profiles.normalize_profile_id(profile_id)
        profile = self.profiles.load(profile_id)
        sources = get_tracking_sources(profile)
        if not sources:
            raise ValueError("Profile không có nguồn theo dõi nào đang bật.")
        platforms = enabled_platform_names(
            profile,
            sources[0].get("platform", "douyin"),
        )
        if not platforms:
            raise ValueError(
                "Profile chưa bật nền tảng đích hợp lệ; nền tảng trùng với "
                "nguồn video luôn bị bỏ qua."
            )
        with self._lock:
            current = self._states.get(profile_id) or {}
            if current.get("active"):
                raise RuntimeError("Profile đang chạy một lượt test đăng video.")
            now = datetime.now().isoformat(timespec="seconds")
            self._states[profile_id] = {
                "profile_id": profile_id,
                "status": "starting",
                "active": True,
                "message": "Đang chuẩn bị lấy video đầu tiên.",
                "video_id": "",
                "platforms": platforms,
                "results": {},
                "last_error": "",
                "started_at": now,
                "updated_at": now,
            }
        threading.Thread(
            target=self._run,
            args=(profile_id, profile, sources[0]),
            daemon=True,
            name=f"profile-{profile_id}-test-upload",
        ).start()
        return self.snapshot()[profile_id]

    def _run(self, profile_id: str, profile: dict[str, Any], source: dict[str, Any]) -> None:
        video_path = ""
        try:
            from application.workflows.profile_worker import (
                download_douyin_video_direct,
                download_tiktok_video_direct,
                resolve_runtime_caption,
            )
            from profile_automation.uploaders.facebook_uploader import FacebookUploader
            from profile_automation.uploaders.tiktok_uploader import TikTokUploader
            from profile_automation.uploaders.youtube_uploader import YouTubeUploader
            from profile_automation.watchers.douyin_profile_monitor import DouyinProfileMonitor
            from profile_automation.watchers.tiktok_profile_monitor import TikTokProfileMonitor

            douyin = profile.get("douyin") or {}
            gemlogin_id = str(douyin.get("gemlogin_profile_id") or profile_id)
            source_platform = str(source.get("platform") or "douyin")
            source_name = source_platform.title()
            self._update(profile_id, status="fetching", message=f"Đang lấy video đầu tiên từ {source_name}.")
            if source_platform == "tiktok":
                monitor = TikTokProfileMonitor(
                    profile_id=profile_id,
                    unique_id=source["unique_id"],
                    sec_uid=source.get("sec_uid", ""),
                    gemlogin_profile_id=gemlogin_id,
                    api_url=config.API_URL,
                    min_likes=0,
                    max_duration_sec=99999,
                    source_key=source["source_key"],
                    profile_config=profile,
                )
            else:
                monitor = DouyinProfileMonitor(
                    profile_id=profile_id,
                    sec_uid=source["target_sec_uid"],
                    gemlogin_profile_id=gemlogin_id,
                    api_url=config.API_URL,
                    min_likes=0,
                    max_duration_sec=99999,
                    source_key=source["source_key"],
                    profile_config=profile,
                )
            videos = monitor.fetch_latest_videos(pages_to_fetch=1)
            if not videos:
                raise RuntimeError(f"Không lấy được video nào từ nguồn {source_name} đầu tiên.")
            video = videos[0]
            self._update(
                profile_id,
                video_id=str(video.aweme_id),
                status="caption",
                message="Đang chuẩn bị mô tả mặc định.",
            )
            profile_for_upload = copy.deepcopy(profile)
            profile_for_upload["_workload_priority"] = 0
            caption = resolve_runtime_caption(profile_for_upload, profile_id, video)
            profile_for_upload["_runtime_caption"] = caption
            self._update(profile_id, status="downloading", message=f"Đang tải video trực tiếp từ {source_name}.")
            downloader = download_tiktok_video_direct if source_platform == "tiktok" else download_douyin_video_direct
            video_path = downloader(
                video=video,
                profile_id=profile_id,
                gemlogin_profile_id=gemlogin_id,
                api_url=config.API_URL,
                priority=0,
                profile_config=profile_for_upload,
            )
            if not video_path:
                raise RuntimeError(f"Không tải được video đầu tiên từ {source_name}.")

            uploaders = {
                "tiktok": ("TikTok", TikTokUploader),
                "facebook": ("Facebook Reels", FacebookUploader),
                "youtube": ("YouTube Shorts", YouTubeUploader),
            }
            results: dict[str, dict[str, Any]] = {}
            for key, (label, uploader_class) in uploaders.items():
                if key not in enabled_platform_names(profile_for_upload, source_platform):
                    continue
                self._update(profile_id, status="uploading", message=f"Đang đăng video test lên {label}.", results=results)
                try:
                    with workload_slot(
                        "upload",
                        profile_id=profile_id,
                        video_id=str(video.aweme_id),
                        platform=key,
                        priority=0,
                    ):
                        ok = bool(uploader_class().upload(video_path, video, profile_for_upload))
                    results[key] = {"ok": ok, "message": "Đăng thành công." if ok else "Đăng thất bại."}
                except Exception as exc:
                    results[key] = {"ok": False, "message": str(exc)}
                self._update(profile_id, results=results)

            succeeded = sum(bool(item.get("ok")) for item in results.values())
            failed = len(results) - succeeded
            self._update(
                profile_id,
                status="completed" if failed == 0 else "completed_with_errors",
                active=False,
                message=f"Hoàn tất test: {succeeded} thành công, {failed} thất bại.",
                results=results,
            )
        except Exception as exc:
            error = str(exc)
            logger.error(f"[ĐĂNG THỬ] Profile {profile_id}: {error}")
            self._update(profile_id, status="failed", active=False, message="Test đăng video thất bại.", last_error=error)
            try:
                from services.integrations.telegram_service import send_error_notification
                send_error_notification(f"Lỗi luồng test đăng video: {error}", profile_id=profile_id)
            except Exception:
                logger.exception("[ĐĂNG THỬ] Không gửi được thông báo lỗi Telegram")
        finally:
            if video_path and os.path.exists(video_path):
                try:
                    os.remove(video_path)
                    logger.info(f"[ĐĂNG THỬ] Đã xóa file tạm: {video_path}")
                except OSError as exc:
                    logger.warning(f"[ĐĂNG THỬ] Không xóa được file tạm {video_path}: {exc}")
