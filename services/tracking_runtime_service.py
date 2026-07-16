from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import core.config as config
from core.utils import logger
from profile_automation.douyin_sources import MAX_NEW_VIDEOS_PER_SOURCE, get_douyin_sources
from services.gemlogin_browser_service import close_gemlogin_profile
from services.telegram_service import send_error_notification
from services.resource_monitor_service import profile_resource_monitor
from services.workload_coordinator import workload_snapshot


RuntimeListener = Callable[[dict], None]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class TrackingRuntimeService:
    """Owns profile tracking threads without depending on a desktop UI toolkit."""

    def __init__(self, profile_dir: str | os.PathLike | None = None):
        self.profile_dir = Path(
            profile_dir
            or Path(config.BASE_DIR) / "profile_automation" / "profiles"
        )
        self._lock = threading.RLock()
        self._states: dict[str, dict] = {}
        self._listeners: list[RuntimeListener] = []
        try:
            from profile_automation.pipeline.video_job_store import VideoJobStore

            VideoJobStore().recover_interrupted_jobs()
        except Exception as exc:
            logger.warning("Không thể chuẩn hóa hàng đợi khi mở runtime desktop: %s", exc)

    def add_listener(self, listener: RuntimeListener) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: RuntimeListener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _public_state(self, profile_id: str, state: dict | None = None) -> dict:
        current = state if state is not None else self._states.get(str(profile_id), {})
        status = str(current.get("status") or "stopped")
        return {
            "profile_id": str(profile_id),
            "status": status,
            "active": status in {"starting", "running", "stopping"},
            "message": str(current.get("message") or "Sẵn sàng"),
            "current_source": str(current.get("current_source") or ""),
            "source_count": int(current.get("source_count") or 0),
            "started_at": str(current.get("started_at") or ""),
            "updated_at": str(current.get("updated_at") or ""),
            "last_error": str(current.get("last_error") or ""),
        }

    def _set_state(self, profile_id: str, **changes) -> dict:
        profile_id = str(profile_id)
        with self._lock:
            state = self._states.setdefault(
                profile_id,
                {
                    "status": "stopped",
                    "message": "Sẵn sàng",
                    "stop_event": None,
                    "thread": None,
                },
            )
            state.update(changes)
            state["updated_at"] = _now()
            snapshot = self._public_state(profile_id, state)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                pass
        return snapshot

    def snapshot(self) -> dict:
        profiles = config.load_profile_configs()
        with self._lock:
            states = {
                str(profile_id): self._public_state(str(profile_id))
                for profile_id in profiles
            }
            for profile_id in self._states:
                states.setdefault(profile_id, self._public_state(profile_id))
        resources = profile_resource_monitor.snapshot(profiles)
        for profile_id, state in states.items():
            state["resources"] = (resources.get("profiles", {}) or {}).get(
                str(profile_id),
                {
                    "ram_mb": 0.0,
                    "cpu_percent": 0.0,
                    "process_count": 0,
                    "browser_pids": [],
                    "gemlogin_ids": [],
                    "debug_addresses": [],
                },
            )
        return {
            "profiles": states,
            "active_profile_ids": sorted(
                profile_id for profile_id, state in states.items() if state["active"]
            ),
            "resources": {
                "generated_at": resources.get("generated_at", ""),
                "system": resources.get("system", {}),
                "workload": workload_snapshot(),
            },
        }

    def active_profile_ids(self) -> set[str]:
        return set(self.snapshot()["active_profile_ids"])

    def start_profile(self, profile_id: str) -> dict:
        profile_id = str(profile_id).strip()
        profile_path = self.profile_dir / f"profile_{profile_id}.json"
        if not profile_id or not profile_path.exists():
            raise FileNotFoundError(f"Không tìm thấy cấu hình Profile {profile_id}.")
        profile = self._load_profile(profile_id)
        if not bool(profile.get("enabled", True)):
            raise ValueError(f"Profile {profile_id} đang bị tắt trong cấu hình.")
        if not get_douyin_sources(profile):
            raise ValueError(f"Profile {profile_id} không có nguồn Douyin nào đang bật.")

        with self._lock:
            existing = self._states.get(profile_id, {})
            if str(existing.get("status")) in {"starting", "running", "stopping"}:
                return self._public_state(profile_id, existing)
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._run_profile,
                args=(profile_id, stop_event),
                daemon=True,
                name=f"tracking-profile-{profile_id}",
            )
            self._states[profile_id] = {
                "status": "starting",
                "message": "Đang kiểm tra GemLogin và tải trực tiếp",
                "current_source": "",
                "source_count": 0,
                "started_at": _now(),
                "updated_at": _now(),
                "last_error": "",
                "stop_event": stop_event,
                "thread": thread,
            }
            snapshot = self._public_state(profile_id, self._states[profile_id])
        logger.info("[Desktop Runtime] Bắt đầu Tracking Profile %s.", profile_id)
        thread.start()
        return snapshot

    def stop_profile(self, profile_id: str, close_browsers: bool = True) -> dict:
        profile_id = str(profile_id).strip()
        with self._lock:
            state = self._states.get(profile_id)
            if not state or str(state.get("status")) not in {"starting", "running", "stopping"}:
                return self._public_state(profile_id, state)
            stop_event = state.get("stop_event")
            if stop_event is not None:
                stop_event.set()
        snapshot = self._set_state(
            profile_id,
            status="stopping",
            message="Đang dừng và đóng cửa sổ Profile",
        )
        logger.info("[Desktop Runtime] Đã gửi lệnh dừng Profile %s.", profile_id)
        if close_browsers:
            self._close_profile_browsers_async(profile_id)
        return snapshot

    def start_all(self) -> dict:
        profiles = config.load_profile_configs()
        started = []
        for profile_id, profile in profiles.items():
            if not bool(profile.get("enabled", True)) or not get_douyin_sources(profile):
                continue
            before = self._states.get(str(profile_id), {}).get("status")
            state = self.start_profile(str(profile_id))
            if before not in {"starting", "running", "stopping"} and state["active"]:
                started.append(str(profile_id))
        return {"started": started, **self.snapshot()}

    def stop_all(self, close_browsers: bool = True) -> dict:
        active_ids = sorted(self.active_profile_ids())
        for profile_id in active_ids:
            self.stop_profile(profile_id, close_browsers=close_browsers)
        return {"stopped": active_ids, **self.snapshot()}

    def shutdown(self) -> dict:
        """Stop workers and synchronously close browsers before the backend exits."""
        active_ids = sorted(self.active_profile_ids())
        for profile_id in active_ids:
            self.stop_profile(profile_id, close_browsers=False)
        for profile_id in active_ids:
            self._close_profile_browsers(profile_id)
        return {"stopped": active_ids, **self.snapshot()}

    def _load_profile(self, profile_id: str) -> dict:
        path = self.profile_dir / f"profile_{profile_id}.json"
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _api_url() -> str:
        return str(config.load_settings().get("API_URL") or config.API_URL)

    def _run_profile(self, profile_id: str, stop_event: threading.Event) -> None:
        failed = False
        try:
            profile = self._load_profile(profile_id)
            gemlogin_id = str(
                (profile.get("douyin", {}) or {}).get("gemlogin_profile_id") or profile_id
            )
            from profile_automation.pipeline.profile_worker import check_douyin_direct_download

            result = check_douyin_direct_download(gemlogin_id, api_url=self._api_url())
            if stop_event.is_set():
                return
            if not result.get("ok"):
                raise RuntimeError(str(result.get("message") or "Preflight thất bại."))

            sources = get_douyin_sources(profile)
            self._set_state(
                profile_id,
                status="running",
                message=f"Đang giám sát {len(sources)} nguồn",
                source_count=len(sources),
                last_error="",
            )
            self._monitor_loop(profile_id, stop_event)
        except Exception as exc:
            failed = True
            error = str(exc)
            logger.exception("[Desktop Runtime] Profile %s dừng do lỗi: %s", profile_id, exc)
            self._set_state(
                profile_id,
                status="error",
                message="Tracking gặp lỗi",
                last_error=error,
                current_source="",
            )
            send_error_notification(
                f"Không thể chạy Tracking Douyin: {error}",
                profile_id=profile_id,
            )
            self._close_profile_browsers_async(profile_id)
        finally:
            with self._lock:
                current = self._states.get(profile_id, {})
                is_current_thread = current.get("stop_event") is stop_event
            if is_current_thread:
                if not failed:
                    self._set_state(
                        profile_id,
                        status="stopped",
                        message="Đã dừng",
                        current_source="",
                        stop_event=None,
                        thread=None,
                    )
                else:
                    with self._lock:
                        current = self._states.get(profile_id, {})
                        current["stop_event"] = None
                        current["thread"] = None
                logger.info("[Desktop Runtime] Luồng Tracking Profile %s đã kết thúc.", profile_id)

    def _monitor_loop(self, profile_id: str, stop_event: threading.Event) -> None:
        from profile_automation.pipeline.profile_worker import (
            ProfileWorker,
            enabled_platform_names,
        )
        from profile_automation.watchers.douyin_profile_monitor import DouyinProfileMonitor
        from services.telegram_service import send_new_video_notification

        next_scan_at: dict[str, float] = {}
        baselined_source_keys: set[str] = set()

        while not stop_event.is_set():
            profile = self._load_profile(profile_id)
            douyin = profile.get("douyin", {}) or {}
            gemlogin_id = str(douyin.get("gemlogin_profile_id") or profile_id)
            sources = get_douyin_sources(profile)
            if not sources:
                raise RuntimeError("Profile không có nguồn Douyin nào đang bật.")

            now = time.time()
            active_keys = {source["source_key"] for source in sources}
            baselined_source_keys.intersection_update(active_keys)
            next_scan_at = {key: value for key, value in next_scan_at.items() if key in active_keys}
            for index, source in enumerate(sources):
                next_scan_at.setdefault(source["source_key"], now + index * 5)
            due_sources = [
                source for source in sources
                if next_scan_at.get(source["source_key"], 0) <= now
            ]
            if not due_sources:
                self._set_state(
                    profile_id,
                    status="running",
                    message=f"Đang giám sát {len(sources)} nguồn",
                    source_count=len(sources),
                    current_source="",
                )
                stop_event.wait(1)
                continue

            worker = ProfileWorker(profile)
            for source in due_sources:
                if stop_event.is_set():
                    break
                source_key = source["source_key"]
                source_label = worker._source_label(source)
                next_scan_at[source_key] = time.time() + source["check_interval_minutes"] * 60
                self._set_state(
                    profile_id,
                    status="running",
                    message=f"Đang quét nguồn {source_label}",
                    current_source=source_label,
                    source_count=len(sources),
                )
                logger.info("[Desktop Runtime] Profile %s quét nguồn %s.", profile_id, source_label)
                try:
                    monitor = DouyinProfileMonitor(
                        profile_id=profile_id,
                        sec_uid=source["target_sec_uid"],
                        gemlogin_profile_id=gemlogin_id,
                        api_url=self._api_url(),
                        min_likes=profile.get("filters", {}).get("min_likes", 0),
                        max_duration_sec=profile.get("filters", {}).get("max_duration_seconds", 300),
                        source_key=source_key,
                        migrate_legacy_state=source.get("migrate_legacy_state", False),
                    )
                    pending_videos = worker.get_pending_videos(source)
                    if source_key not in baselined_source_keys:
                        baseline_videos = monitor.build_start_baseline()
                        if baseline_videos is not None:
                            baselined_source_keys.add(source_key)
                            logger.info(
                                "[Desktop Runtime] Profile %s đã baseline %s video nguồn %s.",
                                profile_id,
                                len(baseline_videos),
                                source_label,
                            )
                        elif not pending_videos:
                            continue
                        if not pending_videos:
                            continue
                        detected_videos = []
                    else:
                        detected_videos = monitor.get_new_videos()

                    pending_ids = {video.aweme_id for video in pending_videos}
                    new_videos = [
                        video for video in detected_videos
                        if video.aweme_id not in pending_ids
                    ]
                    if len(new_videos) > MAX_NEW_VIDEOS_PER_SOURCE:
                        newest = max(new_videos, key=lambda video: video.create_time)
                        for ignored in new_videos:
                            if ignored.aweme_id != newest.aweme_id:
                                worker.mark_ignored_job(
                                    ignored,
                                    source,
                                    monitor,
                                    "Bỏ qua vì trong một lượt quét có video mới hơn.",
                                )
                        new_videos = [newest]

                    for video in new_videos:
                        send_new_video_notification(
                            profile_id,
                            worker.name,
                            source_label,
                            video,
                            enabled_platform_names(profile),
                        )
                        worker.register_video(video, source)

                    for video in pending_videos + new_videos:
                        if stop_event.is_set():
                            break
                        self._set_state(
                            profile_id,
                            status="running",
                            message=f"Đang xử lý video {video.aweme_id}",
                            current_source=source_label,
                        )
                        worker.process_video(video, source, monitor, stop_event=stop_event)
                except Exception as exc:
                    logger.exception(
                        "[Desktop Runtime] Lỗi nguồn %s của Profile %s: %s",
                        source_label,
                        profile_id,
                        exc,
                    )
                    send_error_notification(
                        f"Lỗi nguồn {source_label}: {exc}",
                        profile_id=profile_id,
                    )

            if not stop_event.is_set():
                stop_event.wait(1)

    def _close_profile_browsers_async(self, profile_id: str) -> None:
        threading.Thread(
            target=self._close_profile_browsers,
            args=(str(profile_id),),
            daemon=True,
            name=f"close-profile-{profile_id}",
        ).start()

    def _close_profile_browsers(self, profile_id: str) -> None:
        gemlogin_ids = {str(profile_id)}
        try:
            profile = self._load_profile(profile_id)
            for section in ("douyin", "tiktok", "facebook", "youtube"):
                configured_id = str(
                    (profile.get(section, {}) or {}).get("gemlogin_profile_id") or ""
                ).strip()
                if configured_id:
                    gemlogin_ids.add(configured_id)
        except Exception as exc:
            logger.warning("Không đọc được GemLogin ID của Profile %s: %s", profile_id, exc)

        for gemlogin_id in sorted(gemlogin_ids):
            try:
                close_gemlogin_profile(gemlogin_id, self._api_url())
                logger.info("[Desktop Runtime] Đã đóng cửa sổ GemLogin Profile %s.", gemlogin_id)
            except Exception as exc:
                logger.warning("Không thể đóng GemLogin Profile %s: %s", gemlogin_id, exc)
