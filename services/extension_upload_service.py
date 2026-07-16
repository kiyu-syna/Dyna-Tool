from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.utils import logger
from profile_automation.pipeline.profile_worker import ProfileWorker, get_tracking_download_path
from profile_automation.pipeline.upload_pipeline import enabled_platform_names
from profile_automation.pipeline.video_job_store import VideoJobStore
from profile_automation.watchers.douyin_profile_monitor import DouyinVideo
from services.profile_management_service import ProfileManagementService


ALLOWED_VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".webm"}
EXTENSION_SOURCE_KEY = "dyna_browser_extension"
EXTENSION_SOURCE_LABEL = "Trình duyệt Douyin"


@dataclass(frozen=True)
class ExtensionUploadRequest:
    profile_id: str
    video_id: str
    file_path: str
    source_url: str = ""
    description: str = ""
    create_time: int = 0
    duration_ms: int = 0
    like_count: int = 0
    play_count: int = 0
    author_uid: str = ""
    author_nickname: str = ""
    download_url: str = ""
    download_id: int = 0


class _ExtensionMonitor:
    """ProfileWorker callback target for a manually selected browser video."""

    @staticmethod
    def mark_processed(_video: DouyinVideo) -> None:
        return None

    @staticmethod
    def mark_ignored(_video: DouyinVideo) -> None:
        return None


class ExtensionUploadService:
    """Imports completed Chrome downloads and runs the normal Dyna upload pipeline."""

    def __init__(
        self,
        jobs: VideoJobStore | None = None,
        profiles: ProfileManagementService | None = None,
    ) -> None:
        self.jobs = jobs or VideoJobStore()
        self.profiles = profiles or ProfileManagementService()
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}

    @staticmethod
    def _job_key(profile_id: str, video_id: str) -> str:
        return VideoJobStore.job_key(profile_id, video_id)

    @staticmethod
    def _validate_source_url(source_url: str) -> str:
        value = str(source_url or "").strip()
        if not value:
            return ""
        parsed = urlparse(value)
        hostname = str(parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not (
            hostname == "douyin.com" or hostname.endswith(".douyin.com")
        ):
            raise ValueError("Link nguồn phải là một trang Douyin hợp lệ.")
        return value

    @staticmethod
    def _validate_file_path(file_path: str) -> Path:
        raw_path = str(file_path or "").strip()
        if not raw_path:
            raise ValueError("Extension chưa gửi đường dẫn file video.")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            raise ValueError("Đường dẫn video từ extension phải là đường dẫn tuyệt đối.")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise FileNotFoundError(f"Không tìm thấy file Chrome đã tải: {path}") from exc
        if not resolved.is_file():
            raise FileNotFoundError(f"File Chrome đã tải không tồn tại: {resolved}")
        if resolved.suffix.lower() not in ALLOWED_VIDEO_SUFFIXES:
            raise ValueError(f"Định dạng file không được hỗ trợ: {resolved.suffix or '(không có đuôi)'}")
        if resolved.stat().st_size <= 0:
            raise ValueError("File Chrome đã tải đang rỗng.")
        return resolved

    @staticmethod
    def _video_from_request(request: ExtensionUploadRequest) -> DouyinVideo:
        source_url = ExtensionUploadService._validate_source_url(request.source_url)
        return DouyinVideo(
            aweme_id=str(request.video_id),
            share_url=source_url or f"https://www.douyin.com/video/{request.video_id}",
            desc=str(request.description or ""),
            create_time=max(0, int(request.create_time or time.time())),
            duration_ms=max(0, int(request.duration_ms or 0)),
            like_count=max(0, int(request.like_count or 0)),
            play_count=max(0, int(request.play_count or 0)),
            author_uid=str(request.author_uid or ""),
            author_nickname=str(request.author_nickname or ""),
            download_url=str(request.download_url or ""),
            download_urls=[str(request.download_url)] if request.download_url else [],
        )

    def list_profiles(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.profiles.profile_dir.exists():
            return rows
        for path in sorted(self.profiles.profile_dir.glob("profile_*.json")):
            profile_id = path.stem.removeprefix("profile_")
            try:
                profile = self.profiles.load(profile_id)
            except Exception as exc:
                logger.warning("Could not read Profile %s for the extension: %s", profile_id, exc)
                continue
            platforms = {
                key: bool((profile.get(key) or {}).get("enabled", False))
                for key in ("tiktok", "youtube", "facebook")
            }
            enabled = bool(profile.get("enabled", True))
            available = enabled and any(platforms.values())
            reason = ""
            if not enabled:
                reason = "Profile đang bị tắt."
            elif not any(platforms.values()):
                reason = "Profile chưa bật nền tảng đăng nào."
            rows.append(
                {
                    "id": str(profile_id),
                    "name": str(profile.get("name") or f"Profile {profile_id}"),
                    "enabled": enabled,
                    "available": available,
                    "platforms": platforms,
                    "reason": reason,
                }
            )
        return rows

    def submit(self, request: ExtensionUploadRequest) -> dict[str, Any]:
        profile_id = self.profiles.normalize_profile_id(request.profile_id)
        video_id = str(request.video_id or "").strip()
        if not video_id:
            raise ValueError("Thiếu ID video Douyin.")
        source_path = self._validate_file_path(request.file_path)
        profile = self.profiles.load(profile_id)
        if not bool(profile.get("enabled", True)):
            raise ValueError(f"Profile {profile_id} đang bị tắt.")
        if not enabled_platform_names(profile):
            raise ValueError(f"Profile {profile_id} chưa bật nền tảng đăng video nào.")

        video = self._video_from_request(request)
        source = {
            "source_key": EXTENSION_SOURCE_KEY,
            "target_sec_uid": str(request.author_uid or "extension"),
            "target_display_name": EXTENSION_SOURCE_LABEL,
            "enabled": True,
        }
        worker = ProfileWorker(profile)
        worker.job_store = self.jobs
        job = worker.register_video(video, source)
        status = str(job.get("status") or "")
        if status == "completed":
            return {"accepted": False, "duplicate": True, "job": job}
        if status in {"cancelled", "ignored"}:
            raise ValueError(f"Video này đã có trạng thái {status}; hãy xử lý lại từ Dyna.")
        if bool(job.get("active")):
            return {"accepted": False, "duplicate": True, "job": job}

        key = self._job_key(profile_id, video_id)
        with self._lock:
            running = self._threads.get(key)
            if running and running.is_alive():
                return {"accepted": False, "duplicate": True, "job": self.jobs.get_job(profile_id, video_id)}
            self.jobs.set_status(profile_id, video_id, "importing")
            try:
                self._copy_into_managed_storage(profile_id, video_id, source_path)
            except Exception as exc:
                self.jobs.set_status(
                    profile_id,
                    video_id,
                    "failed_download",
                    error=f"Nhận file từ extension thất bại: {exc}",
                )
                raise RuntimeError(f"Dyna không thể nhận file Chrome đã tải: {exc}") from exc
            self._start_processing(profile_id, profile, video, source, source_path)
        return {"accepted": True, "duplicate": False, "job": self.jobs.get_job(profile_id, video_id)}

    def get_job(self, profile_id: str, video_id: str) -> dict[str, Any] | None:
        return self.jobs.get_job(str(profile_id), str(video_id))

    def _copy_into_managed_storage(
        self,
        profile_id: str,
        video_id: str,
        source_path: Path,
    ) -> Path:
        managed_path = Path(get_tracking_download_path(profile_id, video_id))
        temporary_path = managed_path.with_suffix(managed_path.suffix + ".part")
        managed_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if temporary_path.exists():
                temporary_path.unlink()
            shutil.copy2(source_path, temporary_path)
            if temporary_path.stat().st_size != source_path.stat().st_size:
                raise IOError("Kích thước file sao chép vào Dyna không khớp file Chrome.")
            os.replace(temporary_path, managed_path)
            self.jobs.set_download_path(profile_id, video_id, str(managed_path))
            return managed_path
        finally:
            try:
                if temporary_path.exists():
                    temporary_path.unlink()
            except OSError:
                pass

    def _start_processing(
        self,
        profile_id: str,
        profile: dict[str, Any],
        video: DouyinVideo,
        source: dict[str, Any],
        source_path: Path | None = None,
    ) -> bool:
        key = self._job_key(profile_id, video.aweme_id)
        with self._lock:
            running = self._threads.get(key)
            if running and running.is_alive():
                return False
            thread = threading.Thread(
                target=self._process_managed_upload,
                args=(profile_id, profile, video, source, source_path),
                daemon=True,
                name=f"extension-upload-{profile_id}-{video.aweme_id}",
            )
            self._threads[key] = thread
            thread.start()
        return True

    def _process_managed_upload(
        self,
        profile_id: str,
        profile: dict[str, Any],
        video: DouyinVideo,
        source: dict[str, Any],
        source_path: Path | None = None,
    ) -> None:
        key = self._job_key(profile_id, video.aweme_id)
        try:
            failed = self.jobs.retry_job(profile_id, video.aweme_id)
            if failed is None:
                raise RuntimeError("Không thể đưa video extension trở lại hàng đợi.")

            logger.info(
                "[Extension] Received video %s for Profile %s from %s.",
                video.aweme_id,
                profile_id,
                source_path or "managed storage",
            )
            worker = ProfileWorker(profile)
            worker.job_store = self.jobs
            worker.process_video(video, source, _ExtensionMonitor())
        except Exception as exc:
            logger.exception(
                "[Extension] Could not process video %s for Profile %s: %s",
                video.aweme_id,
                profile_id,
                exc,
            )
            try:
                self.jobs.set_status(
                    profile_id,
                    video.aweme_id,
                    "failed_upload",
                    error=f"Xử lý video từ extension thất bại: {exc}",
                )
            except Exception:
                pass
        finally:
            with self._lock:
                self._threads.pop(key, None)

    def resume_pending_jobs(self) -> int:
        """Resume browser-extension jobs that Dyna owned before its last shutdown."""
        resumed = 0
        for job in self.jobs.list_jobs(include_terminal=False):
            if str(job.get("source_key") or "") != EXTENSION_SOURCE_KEY:
                continue
            if job.get("active") or str(job.get("status") or "").startswith("failed"):
                continue
            download_path = Path(str(job.get("download_path") or ""))
            if not download_path.is_file():
                continue
            profile_id = str(job.get("profile_id") or "")
            try:
                profile = self.profiles.load(profile_id)
                video = DouyinVideo(**dict(job.get("video") or {}))
                source = {
                    "source_key": EXTENSION_SOURCE_KEY,
                    "target_sec_uid": str(video.author_uid or "extension"),
                    "target_display_name": str(job.get("source_label") or EXTENSION_SOURCE_LABEL),
                    "enabled": True,
                }
                if self._start_processing(profile_id, profile, video, source):
                    resumed += 1
            except Exception as exc:
                logger.exception(
                    "[Extension] Could not resume video %s for Profile %s: %s",
                    job.get("video_id"),
                    profile_id,
                    exc,
                )
        return resumed
