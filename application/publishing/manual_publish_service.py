from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.utils import logger
from application.publishing.extension_upload_service import (
    LOCAL_SOURCE_KEY,
    LOCAL_SOURCE_LABEL,
    SUPPORTED_PLATFORMS,
    ExtensionUploadRequest,
    ExtensionUploadService,
)
from application.publishing.publisher_ready_check_service import PublisherReadyCheckService


READY_CHECK_PREFLIGHT = timedelta(minutes=10)


@dataclass(frozen=True)
class ManualPublishTarget:
    profile_id: str
    platforms: tuple[str, ...]


@dataclass(frozen=True)
class ManualPublishItem:
    file_path: str
    caption: str
    scheduled_at: str = ""
    video_id: str = ""
    source_url: str = ""
    source_label: str = ""
    create_time: int = 0
    duration_ms: int = 0
    like_count: int = 0
    play_count: int = 0
    author_uid: str = ""
    author_nickname: str = ""
    download_url: str = ""


@dataclass(frozen=True)
class ManualPublishRequest:
    file_paths: tuple[str, ...] = ()
    caption: str = ""
    batch_name: str = ""
    targets: tuple[ManualPublishTarget, ...] = ()
    items: tuple[ManualPublishItem, ...] = ()


class ManualPublishService:
    """Turns local files into normal Dyna upload jobs."""

    def __init__(
        self,
        ingest: ExtensionUploadService,
        ready_check: PublisherReadyCheckService | None = None,
    ) -> None:
        self.ingest = ingest
        self.ready_check = ready_check or PublisherReadyCheckService(ingest.profiles)
        self._scheduler_lock = threading.RLock()
        self._scheduler_stop = threading.Event()
        self._scheduler_thread: threading.Thread | None = None

    def list_profiles(self) -> list[dict[str, Any]]:
        rows = self.ingest.list_profiles()
        for row in rows:
            platforms = [
                platform
                for platform, enabled in (row.get("platforms") or {}).items()
                if enabled
            ]
            row["readiness"] = (
                self.ready_check.profile_states(str(row["id"]), platforms)
                if platforms
                else {}
            )
        return rows

    def readiness_snapshot(self) -> dict[str, Any]:
        return self.ready_check.snapshot()

    def refresh_readiness(
        self,
        targets: tuple[ManualPublishTarget, ...],
        *,
        force: bool = True,
    ) -> dict[str, Any]:
        return self.ready_check.start_checks(
            ((target.profile_id, target.platforms) for target in targets),
            force=force,
        )

    def list_jobs(self, limit: int = 100) -> dict[str, Any]:
        rows = [
            job
            for job in self.ingest.jobs.list_jobs(include_terminal=True)
            if str(job.get("source_key") or "") == LOCAL_SOURCE_KEY
        ]
        return {"jobs": rows[:limit], "total": len(rows)}

    def submit(self, request: ManualPublishRequest) -> dict[str, Any]:
        requested_items = request.items or tuple(
            ManualPublishItem(file_path=file_path, caption=request.caption)
            for file_path in request.file_paths
        )
        if not requested_items:
            raise ValueError("Hãy chọn ít nhất một video local.")
        if len(requested_items) > 50:
            raise ValueError("Mỗi lần chỉ được chọn tối đa 50 video.")
        if not request.targets:
            raise ValueError("Hãy chọn ít nhất một Profile để đăng.")

        normalized_items: list[tuple[Path, ManualPublishItem, str, str]] = []
        for index, item in enumerate(requested_items, start=1):
            resolved_path = self.ingest._validate_file_path(item.file_path)
            caption = str(item.caption or "").strip()
            if not caption:
                raise ValueError(f"Video {index} chưa có caption.")
            scheduled_at = self._normalize_scheduled_at(item.scheduled_at)
            normalized_items.append((resolved_path, item, caption, scheduled_at))

        profile_rows = {row["id"]: row for row in self.list_profiles()}
        normalized_targets: list[ManualPublishTarget] = []
        seen_profiles: set[str] = set()
        for target in request.targets:
            profile_id = self.ingest.profiles.normalize_profile_id(target.profile_id)
            if profile_id in seen_profiles:
                raise ValueError(f"Profile {profile_id} bị chọn lặp lại.")
            seen_profiles.add(profile_id)
            profile_row = profile_rows.get(profile_id)
            if not profile_row:
                raise ValueError(f"Không tìm thấy Profile {profile_id}.")
            if not profile_row.get("available"):
                raise ValueError(str(profile_row.get("reason") or f"Profile {profile_id} chưa sẵn sàng."))

            platforms = tuple(
                dict.fromkeys(
                    str(item).strip().lower()
                    for item in target.platforms
                    if str(item).strip()
                )
            )
            if not platforms:
                raise ValueError(f"Hãy chọn nền tảng cho Profile {profile_id}.")
            unknown = [item for item in platforms if item not in SUPPORTED_PLATFORMS]
            if unknown:
                raise ValueError(f"Nền tảng không được hỗ trợ: {', '.join(unknown)}")
            unavailable = [
                item
                for item in platforms
                if not bool((profile_row.get("platforms") or {}).get(item, False))
            ]
            if unavailable:
                raise ValueError(
                    f"Profile {profile_id} chưa bật nền tảng: {', '.join(unavailable)}"
                )
            normalized_targets.append(ManualPublishTarget(profile_id, platforms))

        for target in normalized_targets:
            readiness = self.ready_check.ensure_ready(
                target.profile_id,
                target.platforms,
                force=False,
            )
            if not readiness.get("ready"):
                detail = str(readiness.get("message") or "Tài khoản chưa sẵn sàng.")
                raise RuntimeError(f"Kiểm tra sẵn sàng Profile {target.profile_id}: {detail}")

        batch_id = uuid4().hex[:12]
        batch_name = str(request.batch_name or "").strip()[:160]
        if not batch_name:
            batch_name = f"Lô video {datetime.now().astimezone().strftime('%d/%m/%Y %H:%M')}"
        accepted: list[dict[str, Any]] = []
        scheduled_count = 0
        for index, (file_path, item, caption, scheduled_at) in enumerate(normalized_items, start=1):
            video_id = str(item.video_id or "").strip() or f"local_{batch_id}_{index}"
            source_label = str(item.source_label or "").strip() or f"{LOCAL_SOURCE_LABEL} · {file_path.name}"
            if scheduled_at:
                scheduled_count += 1
            for target in normalized_targets:
                result = self.ingest.submit(
                    ExtensionUploadRequest(
                        profile_id=target.profile_id,
                        video_id=video_id,
                        file_path=str(file_path),
                        description=caption,
                        source_url=str(item.source_url or ""),
                        create_time=max(0, int(item.create_time or 0)),
                        duration_ms=max(0, int(item.duration_ms or 0)),
                        like_count=max(0, int(item.like_count or 0)),
                        play_count=max(0, int(item.play_count or 0)),
                        author_uid=str(item.author_uid or ""),
                        author_nickname=str(item.author_nickname or ""),
                        download_url=str(item.download_url or ""),
                        source_key=LOCAL_SOURCE_KEY,
                        source_label=source_label,
                        platforms=target.platforms,
                        use_supplied_caption=True,
                        scheduled_at=scheduled_at,
                        batch_id=batch_id,
                        batch_name=batch_name,
                    )
                )
                accepted.append(
                    {
                        "profile_id": target.profile_id,
                        "video_id": video_id,
                        "file_name": file_path.name,
                        "file_path": str(file_path),
                        "platforms": list(target.platforms),
                        "scheduled_at": scheduled_at,
                        "accepted": bool(result.get("accepted")),
                        "job": result.get("job"),
                    }
                )
        return {
            "ok": True,
            "batch_id": batch_id,
            "batch_name": batch_name,
            "file_count": len(normalized_items),
            "job_count": len(accepted),
            "scheduled_count": scheduled_count,
            "immediate_count": len(normalized_items) - scheduled_count,
            "jobs": accepted,
        }

    @staticmethod
    def _normalize_scheduled_at(value: str, *, now: datetime | None = None) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            scheduled = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Thời gian đặt lịch không hợp lệ.") from exc
        if scheduled.tzinfo is None:
            scheduled = scheduled.astimezone()
        scheduled_utc = scheduled.astimezone(timezone.utc)
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if scheduled_utc <= current + timedelta(seconds=30):
            raise ValueError("Thời gian đặt lịch phải sau hiện tại ít nhất 30 giây.")
        if scheduled_utc > current + timedelta(days=365):
            raise ValueError("Chỉ có thể đặt lịch trong vòng 365 ngày.")
        return scheduled_utc.isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _scheduled_datetime(value: str) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.astimezone(timezone.utc)

    def run_due_jobs(self, *, now: datetime | None = None) -> int:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        started = 0
        for job in self.ingest.jobs.list_jobs(include_terminal=False):
            if str(job.get("source_key") or "") != LOCAL_SOURCE_KEY:
                continue
            if str(job.get("status") or "") != "scheduled" or job.get("active"):
                continue
            scheduled = self._scheduled_datetime(str(job.get("scheduled_at") or ""))
            if scheduled is None or scheduled > current + READY_CHECK_PREFLIGHT:
                continue
            profile_id = str(job.get("profile_id") or "")
            video_id = str(job.get("video_id") or "")
            due_now = scheduled <= current
            try:
                platforms = tuple(str(item) for item in job.get("enabled_platforms") or ())
                readiness = self.ready_check.ensure_ready(
                    profile_id,
                    platforms,
                    force=False,
                )
                if not readiness.get("ready"):
                    error = f"Đang chờ kiểm tra sẵn sàng: {readiness.get('message') or 'tài khoản chưa sẵn sàng.'}"
                    if str(job.get("last_error") or "") != error:
                        self.ingest.jobs.set_status(
                            profile_id,
                            video_id,
                            "scheduled",
                            error=error,
                    )
                    continue
                if not due_now:
                    if str(job.get("last_error") or "").startswith("Đang chờ kiểm tra sẵn sàng:"):
                        self.ingest.jobs.set_status(profile_id, video_id, "scheduled")
                    continue
                result = self.ingest.start_scheduled_job(profile_id, video_id)
                if result is not None:
                    started += 1
            except Exception as exc:
                logger.exception(
                    "[Trung tâm đăng] Không thể bắt đầu video đã lên lịch %s cho Profile %s: %s",
                    video_id,
                    profile_id,
                    exc,
                )
                try:
                    self.ingest.jobs.set_status(
                        profile_id,
                        video_id,
                        "failed_upload" if due_now else "scheduled",
                        error=(
                            f"Không thể bắt đầu lịch đăng: {exc}"
                            if due_now
                            else f"Đang chờ kiểm tra sẵn sàng: {exc}"
                        ),
                    )
                except Exception:
                    pass
        return started

    def start_scheduler(self) -> bool:
        with self._scheduler_lock:
            if self._scheduler_thread and self._scheduler_thread.is_alive():
                return False
            self._scheduler_stop = threading.Event()
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop,
                daemon=True,
                name="publish-center-scheduler",
            )
            self._scheduler_thread.start()
            return True

    def _scheduler_loop(self) -> None:
        self.run_due_jobs()
        while not self._scheduler_stop.wait(2):
            self.run_due_jobs()

    def stop_scheduler(self) -> None:
        with self._scheduler_lock:
            thread = self._scheduler_thread
            self._scheduler_stop.set()
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        with self._scheduler_lock:
            if self._scheduler_thread is thread:
                self._scheduler_thread = None
        self.ready_check.stop()

    def retry(self, profile_id: str, video_id: str) -> dict[str, Any] | None:
        current = self.ingest.jobs.get_job(str(profile_id), str(video_id))
        if current is None or str(current.get("source_key") or "") != LOCAL_SOURCE_KEY:
            return None
        platforms = tuple(str(item) for item in current.get("enabled_platforms") or ())
        readiness = self.ready_check.ensure_ready(str(profile_id), platforms, force=False)
        if not readiness.get("ready"):
            raise RuntimeError(
                f"Kiểm tra sẵn sàng Profile {profile_id}: {readiness.get('message') or 'tài khoản chưa sẵn sàng.'}"
            )
        return self.ingest.retry_imported_job(str(profile_id), str(video_id))

    def cancel(self, profile_id: str, video_id: str) -> dict[str, Any] | None:
        current = self.ingest.jobs.get_job(str(profile_id), str(video_id))
        if current is None or str(current.get("source_key") or "") != LOCAL_SOURCE_KEY:
            return None
        return self.ingest.jobs.cancel_job(
            str(profile_id),
            str(video_id),
            reason="Người dùng hủy video từ Publish Center.",
        )

    def delete(self, profile_id: str, video_id: str) -> dict[str, Any] | None:
        current = self.ingest.jobs.get_job(str(profile_id), str(video_id))
        if current is None or str(current.get("source_key") or "") != LOCAL_SOURCE_KEY:
            return None
        return self.ingest.jobs.dismiss_job(
            str(profile_id),
            str(video_id),
            reason="Người dùng xóa video khỏi hàng đợi Publish Center.",
        )

    def reset_jobs(self) -> dict[str, Any]:
        return self.ingest.jobs.dismiss_jobs(
            source_key=LOCAL_SOURCE_KEY,
            reason="Người dùng reset hàng đợi Publish Center.",
        )
