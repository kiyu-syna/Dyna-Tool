import copy
import ctypes
import json
import os
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import core.config as config
from core.runtime_paths import profile_state_dir
from core.utils import logger
from profile_automation.watchers.douyin_video import DouyinVideo
from profile_automation.watchers.tiktok_profile_monitor import TikTokVideo
from services.runtime.activity_history_service import ActivityHistoryStore


SCHEMA_VERSION = 1
PLATFORMS = ("tiktok", "facebook", "youtube")
TERMINAL_STATUSES = {"completed", "cancelled", "ignored"}
TERMINAL_JOB_RETENTION_DAYS = 30
MAX_RETAINED_TERMINAL_JOBS = 500
_STORE_LOCK = threading.RLock()
_PROCESS_INSTANCE_ID = uuid.uuid4().hex


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _pid_is_running(pid: int) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            int(pid),
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True

    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False


class VideoJobStore:
    """Persistent, thread-safe state for the cross-platform video queue."""

    def __init__(self, path: Optional[str] = None):
        use_default_path = path is None
        self.path = Path(
            path
            or profile_state_dir() / "video_jobs.json"
        )
        self.history = None
        if use_default_path:
            try:
                self.history = ActivityHistoryStore()
            except Exception as exc:
                logger.warning("Không thể mở kho lịch sử thống kê; hàng đợi vẫn tiếp tục: %s", exc)

    def _record_history(
        self,
        profile_id: str,
        video_id: str,
        event_type: str,
        *,
        status: str = "",
        platform: str = "",
        error: str = "",
    ) -> None:
        if self.history is not None:
            self.history.record_event(
                profile_id,
                video_id,
                event_type,
                status=status,
                platform=platform,
                error=error,
            )

    @staticmethod
    def job_key(profile_id: str, video_id: str) -> str:
        return f"{str(profile_id)}:{str(video_id)}"

    def _empty_data(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "jobs": {}}

    def _load_unlocked(self) -> dict:
        if not self.path.exists():
            return self._empty_data()
        try:
            with open(self.path, "r", encoding="utf-8") as file_handle:
                data = json.load(file_handle)
            if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
                raise ValueError("invalid video job store format")
            data["schema_version"] = SCHEMA_VERSION
            return data
        except Exception as exc:
            logger.error("Không thể đọc kho trạng thái video %s: %s", self.path, exc)
            return self._empty_data()

    def _save_unlocked(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(temp_path, "w", encoding="utf-8") as file_handle:
            json.dump(data, file_handle, ensure_ascii=False, indent=2)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temp_path, self.path)

    @staticmethod
    def _prune_terminal_jobs_unlocked(
        data: dict,
        *,
        retention_days: int = TERMINAL_JOB_RETENTION_DAYS,
        max_terminal_jobs: int = MAX_RETAINED_TERMINAL_JOBS,
        now: Optional[datetime] = None,
    ) -> int:
        jobs = data.get("jobs", {})
        if not isinstance(jobs, dict):
            return 0
        current_time = now or datetime.now()
        cutoff = current_time - timedelta(days=max(1, int(retention_days)))
        terminal_items = []
        remove_keys = set()

        for key, job in jobs.items():
            if job.get("status") not in TERMINAL_STATUSES:
                continue
            timestamp = str(job.get("updated_at") or job.get("created_at") or "")
            try:
                updated_at = datetime.fromisoformat(timestamp)
            except (TypeError, ValueError):
                updated_at = current_time
            if updated_at < cutoff:
                remove_keys.add(key)
            else:
                terminal_items.append((updated_at, key))

        terminal_items.sort(reverse=True)
        keep_count = max(0, int(max_terminal_jobs))
        remove_keys.update(key for _, key in terminal_items[keep_count:])
        for key in remove_keys:
            jobs.pop(key, None)
        return len(remove_keys)

    def prune_terminal_jobs(
        self,
        *,
        retention_days: int = TERMINAL_JOB_RETENTION_DAYS,
        max_terminal_jobs: int = MAX_RETAINED_TERMINAL_JOBS,
        now: Optional[datetime] = None,
    ) -> int:
        """Remove only old terminal jobs; failed and pending jobs are always preserved."""
        with _STORE_LOCK:
            data = self._load_unlocked()
            removed = self._prune_terminal_jobs_unlocked(
                data,
                retention_days=retention_days,
                max_terminal_jobs=max_terminal_jobs,
                now=now,
            )
            if removed:
                self._save_unlocked(data)
        return removed

    @staticmethod
    def _enabled_platforms(profile: dict, source_platform: str = "") -> list[str]:
        enabled = []
        if profile.get("tiktok", {}).get("enabled", True):
            enabled.append("tiktok")
        if profile.get("facebook", {}).get("enabled", False):
            enabled.append("facebook")
        if profile.get("youtube", {}).get("enabled", False):
            enabled.append("youtube")
        normalized_source = str(source_platform or "").strip().casefold()
        return [platform for platform in enabled if platform != normalized_source]

    def ensure_job(
        self,
        profile_id: str,
        video: DouyinVideo,
        profile: dict,
        source_key: str = "",
        source_label: str = "",
        source_platform: str = "douyin",
    ) -> dict:
        key = self.job_key(profile_id, video.aweme_id)
        source_platform = str(source_platform or "douyin").strip().casefold()
        enabled_platforms = self._enabled_platforms(profile, source_platform)
        timestamp = _now()
        created = False

        with _STORE_LOCK:
            data = self._load_unlocked()
            self._prune_terminal_jobs_unlocked(data)
            jobs = data["jobs"]
            job = jobs.get(key)
            if job is None:
                created = True
                job = {
                    "key": key,
                    "profile_id": str(profile_id),
                    "video_id": str(video.aweme_id),
                    "source_key": str(source_key or ""),
                    "source_label": str(source_label or ""),
                    "source_platform": source_platform,
                    "batch_id": "",
                    "batch_name": "",
                    "video": asdict(video),
                    "status": "detected",
                    "caption": "",
                    "caption_resolved": False,
                    "caption_request_id": "",
                    "download_path": "",
                    "media_info": {},
                    "platforms": {},
                    "attempts": {"caption": 0, "download": 0},
                    "last_error": "",
                    "active": False,
                    "owner_pid": 0,
                    "owner_instance_id": "",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
                jobs[key] = job
            else:
                job["video"] = asdict(video)
                if source_key:
                    job["source_key"] = str(source_key)
                if source_label:
                    job["source_label"] = str(source_label)
                if source_platform:
                    job["source_platform"] = source_platform

            job["enabled_platforms"] = enabled_platforms
            platforms = job.setdefault("platforms", {})
            for platform in PLATFORMS:
                platform_state = platforms.setdefault(
                    platform,
                    {"status": "pending", "attempts": 0, "last_error": "", "updated_at": timestamp},
                )
                if platform not in enabled_platforms and platform_state.get("status") != "success":
                    platform_state["status"] = "disabled"
                elif platform in enabled_platforms and platform_state.get("status") == "disabled":
                    platform_state["status"] = "pending"

            job["updated_at"] = timestamp
            self._save_unlocked(data)
            result = copy.deepcopy(job)
        if created:
            self._record_history(profile_id, video.aweme_id, "detected", status="detected")
        return result

    def get_job(self, profile_id: str, video_id: str) -> Optional[dict]:
        key = self.job_key(profile_id, video_id)
        with _STORE_LOCK:
            job = self._load_unlocked()["jobs"].get(key)
            return copy.deepcopy(job) if job else None

    def list_jobs(
        self,
        profile_id: Optional[str] = None,
        include_terminal: bool = True,
        include_dismissed: bool = False,
    ) -> list[dict]:
        with _STORE_LOCK:
            jobs = list(self._load_unlocked()["jobs"].values())
        if not include_dismissed:
            jobs = [job for job in jobs if not job.get("dismissed_at")]
        if profile_id is not None:
            jobs = [job for job in jobs if job.get("profile_id") == str(profile_id)]
        if not include_terminal:
            jobs = [job for job in jobs if job.get("status") not in TERMINAL_STATUSES]
        jobs.sort(key=lambda job: (job.get("updated_at", ""), job.get("created_at", "")), reverse=True)
        return copy.deepcopy(jobs)

    def list_pending_videos(
        self,
        profile_id: str,
        source_key: str = "",
        source_platform: str = "",
    ) -> list:
        jobs = self.list_jobs(profile_id=profile_id, include_terminal=False)
        jobs = [
            job
            for job in jobs
            if not str(job.get("status") or "").startswith("failed")
        ]
        if source_key:
            jobs = [job for job in jobs if job.get("source_key") == str(source_key)]
        if source_platform:
            jobs = [
                job for job in jobs
                if str(job.get("source_platform") or "douyin") == str(source_platform)
            ]
        jobs.sort(key=lambda job: int(job.get("video", {}).get("create_time", 0)))
        videos = []
        for job in jobs:
            video_data = job.get("video", {})
            try:
                video_type = (
                    TikTokVideo
                    if str(job.get("source_platform") or "douyin") == "tiktok"
                    else DouyinVideo
                )
                videos.append(video_type(**video_data))
            except (TypeError, ValueError) as exc:
                logger.error("Video job %s có metadata không hợp lệ: %s", job.get("key"), exc)
        return videos

    def recover_interrupted_jobs(self) -> dict:
        """Release stale leases and normalize states left mid-operation."""
        recovered_jobs = 0
        recovered_profiles = set()
        current_pid = os.getpid()
        with _STORE_LOCK:
            data = self._load_unlocked()
            for job in data["jobs"].values():
                if job.get("status") in TERMINAL_STATUSES:
                    continue

                owner_pid = int(job.get("owner_pid") or 0)
                owner_instance = str(job.get("owner_instance_id") or "")
                owned_by_other_live_process = bool(
                    job.get("active")
                    and owner_instance != _PROCESS_INSTANCE_ID
                    and owner_pid != current_pid
                    and _pid_is_running(owner_pid)
                )
                if owned_by_other_live_process:
                    continue

                changed = False
                if job.get("active"):
                    job["active"] = False
                    job["owner_pid"] = 0
                    job["owner_instance_id"] = ""
                    changed = True

                platforms = job.setdefault("platforms", {})
                for platform_state in platforms.values():
                    if platform_state.get("status") == "uploading":
                        platform_state["status"] = "pending"
                        platform_state["last_error"] = ""
                        platform_state["updated_at"] = _now()
                        changed = True

                status = str(job.get("status") or "detected")
                if status == "downloading":
                    next_status = "caption_ready" if job.get("caption_resolved") else "detected"
                    job["status"] = next_status
                    changed = True
                elif status == "waiting_caption":
                    if job.get("caption_resolved"):
                        job["status"] = "caption_ready"
                        changed = True
                    elif not str(job.get("caption_request_id") or "").strip():
                        job["status"] = "detected"
                        changed = True
                elif status == "uploading":
                    download_path = str(job.get("download_path") or "")
                    if download_path and os.path.isfile(download_path):
                        job["status"] = "downloaded"
                    else:
                        job["download_path"] = ""
                        job["status"] = "caption_ready" if job.get("caption_resolved") else "detected"
                    changed = True

                if changed:
                    job["last_error"] = ""
                    job["updated_at"] = _now()
                    recovered_jobs += 1
                    recovered_profiles.add(str(job.get("profile_id") or ""))

            if recovered_jobs:
                self._save_unlocked(data)
        return {
            "jobs": recovered_jobs,
            "profile_ids": sorted(profile_id for profile_id in recovered_profiles if profile_id),
        }

    def pending_profile_ids(self) -> list[str]:
        profile_ids = {
            str(job.get("profile_id") or "")
            for job in self.list_jobs(include_terminal=False)
            if job.get("profile_id") not in (None, "")
        }
        return sorted(profile_ids)

    def claim(self, profile_id: str, video_id: str) -> bool:
        key = self.job_key(profile_id, video_id)
        current_pid = os.getpid()
        with _STORE_LOCK:
            data = self._load_unlocked()
            job = data["jobs"].get(key)
            if not job or job.get("dismissed_at"):
                return False
            if str(job.get("status") or "").startswith("failed"):
                return False
            owner_pid = int(job.get("owner_pid") or 0)
            owner_instance_id = str(job.get("owner_instance_id") or "")
            if job.get("active"):
                if owner_instance_id == _PROCESS_INSTANCE_ID:
                    return False
                if owner_pid != current_pid and _pid_is_running(owner_pid):
                    return False
            job["active"] = True
            job["owner_pid"] = current_pid
            job["owner_instance_id"] = _PROCESS_INSTANCE_ID
            job["updated_at"] = _now()
            self._save_unlocked(data)
            return True

    def release(self, profile_id: str, video_id: str) -> None:
        key = self.job_key(profile_id, video_id)
        with _STORE_LOCK:
            data = self._load_unlocked()
            job = data["jobs"].get(key)
            if not job:
                return
            if int(job.get("owner_pid") or 0) not in (0, os.getpid()):
                return
            job["active"] = False
            job["owner_pid"] = 0
            job["owner_instance_id"] = ""
            job["updated_at"] = _now()
            self._save_unlocked(data)

    def set_status(
        self,
        profile_id: str,
        video_id: str,
        status: str,
        error: str = "",
        increment_attempt: str = "",
    ) -> dict:
        key = self.job_key(profile_id, video_id)
        with _STORE_LOCK:
            data = self._load_unlocked()
            job = data["jobs"].get(key)
            if not job:
                raise KeyError(key)
            job["status"] = str(status)
            job["last_error"] = str(error or "")
            if increment_attempt:
                attempts = job.setdefault("attempts", {})
                attempts[increment_attempt] = int(attempts.get(increment_attempt, 0)) + 1
            job["updated_at"] = _now()
            self._save_unlocked(data)
            result = copy.deepcopy(job)
        self._record_history(
            profile_id,
            video_id,
            "status",
            status=str(status),
            error=str(error or ""),
        )
        return result

    def set_caption(self, profile_id: str, video_id: str, caption: str) -> None:
        key = self.job_key(profile_id, video_id)
        with _STORE_LOCK:
            data = self._load_unlocked()
            job = data["jobs"].get(key)
            if not job:
                raise KeyError(key)
            job["caption"] = str(caption or "")
            job["caption_resolved"] = True
            job["caption_request_id"] = ""
            job["status"] = "caption_ready"
            job["last_error"] = ""
            job["updated_at"] = _now()
            self._save_unlocked(data)
        self._record_history(profile_id, video_id, "status", status="caption_ready")

    def set_batch(
        self,
        profile_id: str,
        video_id: str,
        *,
        batch_id: str,
        batch_name: str,
    ) -> None:
        key = self.job_key(profile_id, video_id)
        with _STORE_LOCK:
            data = self._load_unlocked()
            job = data["jobs"].get(key)
            if not job:
                raise KeyError(key)
            job["batch_id"] = str(batch_id or "")[:64]
            job["batch_name"] = str(batch_name or "").strip()[:160]
            job["updated_at"] = _now()
            self._save_unlocked(data)

    def set_caption_request(
        self,
        profile_id: str,
        video_id: str,
        request_id: str,
    ) -> None:
        key = self.job_key(profile_id, video_id)
        with _STORE_LOCK:
            data = self._load_unlocked()
            job = data["jobs"].get(key)
            if not job:
                raise KeyError(key)
            job["caption_request_id"] = str(request_id or "")
            job["caption_resolved"] = False
            job["status"] = "waiting_caption"
            job["last_error"] = ""
            attempts = job.setdefault("attempts", {})
            attempts["caption"] = int(attempts.get("caption", 0)) + 1
            job["updated_at"] = _now()
            self._save_unlocked(data)
        self._record_history(
            profile_id,
            video_id,
            "status",
            status="waiting_caption",
        )

    def set_schedule(self, profile_id: str, video_id: str, scheduled_at: str) -> dict:
        key = self.job_key(profile_id, video_id)
        with _STORE_LOCK:
            data = self._load_unlocked()
            job = data["jobs"].get(key)
            if not job:
                raise KeyError(key)
            job["scheduled_at"] = str(scheduled_at or "")
            job["status"] = "scheduled" if scheduled_at else job.get("status", "downloaded")
            job["last_error"] = ""
            job["updated_at"] = _now()
            self._save_unlocked(data)
            result = copy.deepcopy(job)
        self._record_history(profile_id, video_id, "status", status=str(result["status"]))
        return result

    def set_download_path(self, profile_id: str, video_id: str, download_path: str) -> None:
        key = self.job_key(profile_id, video_id)
        with _STORE_LOCK:
            data = self._load_unlocked()
            job = data["jobs"].get(key)
            if not job:
                raise KeyError(key)
            job["download_path"] = os.path.normpath(download_path) if download_path else ""
            job["status"] = "downloaded" if download_path else job.get("status", "detected")
            job["last_error"] = ""
            job["updated_at"] = _now()
            self._save_unlocked(data)
            recorded_status = str(job.get("status") or "")
        self._record_history(profile_id, video_id, "status", status=recorded_status)

    def set_media_info(self, profile_id: str, video_id: str, media_info: dict) -> None:
        key = self.job_key(profile_id, video_id)
        with _STORE_LOCK:
            data = self._load_unlocked()
            job = data["jobs"].get(key)
            if not job:
                raise KeyError(key)
            job["media_info"] = copy.deepcopy(media_info or {})
            job["updated_at"] = _now()
            self._save_unlocked(data)

    def set_platform_status(
        self,
        profile_id: str,
        video_id: str,
        platform: str,
        status: str,
        error: str = "",
    ) -> None:
        if platform not in PLATFORMS:
            raise ValueError(f"Unknown platform: {platform}")
        key = self.job_key(profile_id, video_id)
        with _STORE_LOCK:
            data = self._load_unlocked()
            job = data["jobs"].get(key)
            if not job:
                raise KeyError(key)
            platform_state = job.setdefault("platforms", {}).setdefault(platform, {})
            if status == "uploading":
                platform_state["attempts"] = int(platform_state.get("attempts", 0)) + 1
            platform_state["status"] = str(status)
            platform_state["last_error"] = str(error or "")
            platform_state["updated_at"] = _now()
            job["updated_at"] = _now()
            self._save_unlocked(data)
        self._record_history(
            profile_id,
            video_id,
            "platform",
            status=str(status),
            platform=platform,
            error=str(error or ""),
        )

    def retry_job(self, profile_id: str, video_id: str) -> Optional[dict]:
        """Requeue a failed job while preserving completed platform uploads."""
        key = self.job_key(profile_id, video_id)
        with _STORE_LOCK:
            data = self._load_unlocked()
            job = data["jobs"].get(key)
            if not job or (job.get("active") and not str(job.get("status", "")).startswith("failed")):
                return None
            if not str(job.get("status", "")).startswith("failed"):
                return copy.deepcopy(job)

            enabled_platforms = set(job.get("enabled_platforms", []))
            for platform, platform_state in job.setdefault("platforms", {}).items():
                if platform not in enabled_platforms:
                    platform_state["status"] = "disabled"
                elif platform_state.get("status") not in {"success", "skipped"}:
                    platform_state["status"] = "pending"
                    platform_state["last_error"] = ""
                platform_state["updated_at"] = _now()

            if job.get("download_path"):
                next_status = "downloaded"
            elif job.get("caption_resolved"):
                next_status = "caption_ready"
            else:
                next_status = "detected"
            job["status"] = next_status
            job["last_error"] = ""
            job["updated_at"] = _now()
            self._save_unlocked(data)
            result = copy.deepcopy(job)
        self._record_history(profile_id, video_id, "retry", status=next_status)
        return result

    def cancel_job(self, profile_id: str, video_id: str, reason: str = "") -> Optional[dict]:
        """Cancel a queued job, or stop further platform uploads for an active upload."""
        key = self.job_key(profile_id, video_id)
        with _STORE_LOCK:
            data = self._load_unlocked()
            job = data["jobs"].get(key)
            if not job:
                return None
            status = str(job.get("status", ""))
            if job.get("active") and status != "uploading" and not status.startswith("failed"):
                return None
            if job.get("status") in TERMINAL_STATUSES:
                return copy.deepcopy(job)
            job["status"] = "cancelled"
            job["last_error"] = str(reason or "Người dùng hủy video.")
            job["updated_at"] = _now()
            self._save_unlocked(data)
            result = copy.deepcopy(job)
        self._record_history(
            profile_id,
            video_id,
            "status",
            status="cancelled",
            error=str(reason or "Người dùng hủy video."),
        )
        return result

    def dismiss_job(self, profile_id: str, video_id: str, reason: str = "") -> Optional[dict]:
        """Hide an inactive job from queues without deleting the user's media file."""
        key = self.job_key(profile_id, video_id)
        with _STORE_LOCK:
            data = self._load_unlocked()
            job = data["jobs"].get(key)
            if not job:
                return None
            if job.get("active"):
                raise RuntimeError("Video đang được xử lý; hãy đợi hoàn tất hoặc hủy trước khi xóa.")
            timestamp = _now()
            job["status"] = "ignored"
            job["dismissed_at"] = timestamp
            job["last_error"] = str(reason or "Người dùng xóa video khỏi hàng đợi.")
            job["owner_pid"] = 0
            job["owner_instance_id"] = ""
            job["updated_at"] = timestamp
            self._save_unlocked(data)
            result = copy.deepcopy(job)
        self._record_history(
            profile_id,
            video_id,
            "status",
            status="ignored",
            error=str(reason or "Người dùng xóa video khỏi hàng đợi."),
        )
        return result

    def dismiss_jobs(self, *, source_key: str | None = None, reason: str = "") -> dict:
        """Hide an entire queue atomically; active jobs must be stopped first."""
        with _STORE_LOCK:
            data = self._load_unlocked()
            jobs = [
                job
                for job in data["jobs"].values()
                if not job.get("dismissed_at")
                and (source_key is None or str(job.get("source_key") or "") == str(source_key))
            ]
            active_jobs = [job for job in jobs if job.get("active")]
            if active_jobs:
                raise RuntimeError(
                    f"Có {len(active_jobs)} video đang được xử lý. Hãy dừng Profile trước khi reset hàng đợi."
                )
            timestamp = _now()
            reset_reason = str(reason or "Người dùng reset hàng đợi video.")
            for job in jobs:
                job["status"] = "ignored"
                job["dismissed_at"] = timestamp
                job["last_error"] = reset_reason
                job["owner_pid"] = 0
                job["owner_instance_id"] = ""
                job["updated_at"] = timestamp
            if jobs:
                self._save_unlocked(data)

        for job in jobs:
            self._record_history(
                str(job.get("profile_id") or ""),
                str(job.get("video_id") or ""),
                "status",
                status="ignored",
                error=reset_reason,
            )
        return {"reset_count": len(jobs)}

    def successful_platforms(self, profile_id: str, video_id: str) -> set[str]:
        job = self.get_job(profile_id, video_id) or {}
        return {
            platform
            for platform, state in job.get("platforms", {}).items()
            if state.get("status") in {"success", "skipped"}
        }
