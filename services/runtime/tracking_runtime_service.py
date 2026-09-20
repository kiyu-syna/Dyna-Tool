from __future__ import annotations

import json
import os
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

import core.config as config
from core.utils import logger
from application.tracking.sources import (
    MAX_NEW_VIDEOS_PER_SOURCE,
    get_tracking_sources,
    tracking_source_label,
)
from services.browser.browser_profile_service import (
    browser_provider,
    close_browser_profile as close_gemlogin_profile,
    connected_browser_profile,
    create_background_page,
    is_browser_connection_error,
)
from services.integrations.telegram_service import send_error_notification
from services.runtime.resource_monitor_service import profile_resource_monitor
from services.runtime.workload_coordinator import workload_snapshot


RuntimeListener = Callable[[dict], None]
PROFILE_START_STAGGER_SECONDS = 5.0
SCAN_JITTER_MIN_SECONDS = 5.0
SCAN_JITTER_MAX_SECONDS = 15.0


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class PersistentWatcherSession:
    """Duy trì phiên trình duyệt và page liên tục để quét các nguồn không cần bật/tắt lại."""

    def __init__(self, profile_id: str, profile_config: dict, api_url: str):
        self.profile_id = str(profile_id)
        self.profile_config = profile_config
        self.api_url = api_url
        self._cm = None
        self.browser = None
        self.page = None
        self._lock = threading.RLock()

    def is_alive(self) -> bool:
        with self._lock:
            if self.page is None or self.browser is None:
                return False
            try:
                if self.page.is_closed():
                    return False
                if hasattr(self.browser, "is_connected") and not self.browser.is_connected():
                    return False
                return True
            except Exception:
                return False

    def acquire_page(self, platform: str = "douyin"):
        with self._lock:
            if self.is_alive():
                return self.page

            self._close_locked()
            try:
                browser_profile_id = str(
                    (self.profile_config.get(platform, {}) or {}).get("gemlogin_profile_id")
                    or (self.profile_config.get("douyin", {}) or {}).get("gemlogin_profile_id")
                    or self.profile_id
                )
                self._cm = connected_browser_profile(
                    browser_profile_id,
                    self.api_url,
                    profile_config=self.profile_config,
                    resource_saving=False,
                    close_profile_on_exit=False,
                )
                self.browser = self._cm.__enter__()
                if not self.browser.contexts:
                    raise RuntimeError(
                        f"CDP đã kết nối nhưng không có context cho profile {self.profile_id}."
                    )
                context = self.browser.contexts[0]
                self.page = create_background_page(self.browser, context)
                logger.info(
                    "[WatcherSession %s] Đã mở phiên trình duyệt quét liên tục.",
                    self.profile_id,
                )
                return self.page
            except Exception as exc:
                self._close_locked()
                logger.warning(
                    "[WatcherSession %s] Không thể mở phiên trình duyệt quét liên tục: %s",
                    self.profile_id,
                    exc,
                )
                return None

    def _close_locked(self) -> None:
        if self.page is not None:
            try:
                if not self.page.is_closed():
                    self.page.close()
            except Exception:
                pass
            self.page = None
        if self._cm is not None:
            try:
                self._cm.__exit__(None, None, None)
            except Exception:
                pass
            self._cm = None
            self.browser = None

    def close(self) -> None:
        with self._lock:
            self._close_locked()


class TrackingRuntimeService:
    """Owns profile tracking threads without depending on a desktop UI toolkit."""

    def __init__(
        self,
        profile_dir: str | os.PathLike | None = None,
        *,
        jitter_source: Callable[[float, float], float] | None = None,
    ):
        self.profile_dir = Path(
            profile_dir
            or Path(config.BASE_DIR) / "profile_automation" / "profiles"
        )
        self._lock = threading.RLock()
        self._states: dict[str, dict] = {}
        self._listeners: list[RuntimeListener] = []
        self._jitter_source = jitter_source or random.uniform
        self._next_profile_start_at = 0.0
        try:
            from application.workflows.video_job_store import VideoJobStore

            VideoJobStore().recover_interrupted_jobs()
        except Exception as exc:
            logger.warning("Không thể chuẩn hóa hàng đợi khi mở tiến trình theo dõi: %s", exc)

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
        source_health = {
            str(key): dict(value)
            for key, value in dict(current.get("source_health") or {}).items()
        }
        successful_scans = [
            str(item.get("last_success_at") or "")
            for item in source_health.values()
            if item.get("last_success_at")
        ]
        return {
            "profile_id": str(profile_id),
            "status": status,
            "active": status in {"starting", "running", "degraded", "stopping"},
            "message": str(current.get("message") or "Sẵn sàng"),
            "current_source": str(current.get("current_source") or ""),
            "source_count": int(current.get("source_count") or 0),
            "healthy_source_count": sum(
                bool(item.get("last_success_at"))
                and int(item.get("consecutive_failures") or 0) == 0
                for item in source_health.values()
            ),
            "failing_source_count": sum(
                int(item.get("consecutive_failures") or 0) > 0
                for item in source_health.values()
            ),
            "last_successful_scan_at": max(successful_scans, default=""),
            "source_health": source_health,
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
        if not get_tracking_sources(profile):
            raise ValueError(f"Profile {profile_id} không có nguồn theo dõi nào đang bật.")

        with self._lock:
            existing = self._states.get(profile_id, {})
            if str(existing.get("status")) in {"starting", "running", "degraded", "stopping"}:
                return self._public_state(profile_id, existing)
            startup_delay_seconds = self._reserve_profile_start_delay_locked()
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._run_profile,
                args=(profile_id, stop_event, startup_delay_seconds),
                daemon=True,
                name=f"tracking-profile-{profile_id}",
            )
            self._states[profile_id] = {
                "status": "starting",
                "message": (
                    f"Chờ {startup_delay_seconds:.0f} giây để quét lệch CPU"
                    if startup_delay_seconds > 0
                    else "Đang kiểm tra trình duyệt và nguồn theo dõi"
                ),
                "current_source": "",
                "source_count": 0,
                "started_at": _now(),
                "updated_at": _now(),
                "last_error": "",
                "source_health": {},
                "stop_event": stop_event,
                "thread": thread,
                "startup_delay_seconds": startup_delay_seconds,
            }
            snapshot = self._public_state(profile_id, self._states[profile_id])
        logger.info("[Tiến trình theo dõi] Bắt đầu theo dõi Profile %s.", profile_id)
        thread.start()
        return snapshot

    def _jitter_seconds(self, minimum: float, maximum: float) -> float:
        try:
            value = float(self._jitter_source(minimum, maximum))
        except Exception:
            value = (minimum + maximum) / 2
        return max(minimum, min(maximum, value))

    def _reserve_profile_start_delay_locked(self) -> float:
        now = time.monotonic()
        has_active_profile = any(
            str(state.get("status")) in {"starting", "running", "degraded", "stopping"}
            for state in self._states.values()
        )
        if not has_active_profile:
            self._next_profile_start_at = now
            return 0.0
        scheduled_at = max(now, self._next_profile_start_at) + PROFILE_START_STAGGER_SECONDS
        self._next_profile_start_at = scheduled_at
        return max(0.0, scheduled_at - now)

    def stop_profile(self, profile_id: str, close_browsers: bool = True) -> dict:
        profile_id = str(profile_id).strip()
        with self._lock:
            state = self._states.get(profile_id)
            if not state or str(state.get("status")) not in {"starting", "running", "degraded", "stopping"}:
                return self._public_state(profile_id, state)
            stop_event = state.get("stop_event")
            if stop_event is not None:
                stop_event.set()
        snapshot = self._set_state(
            profile_id,
            status="stopping",
            message="Đang dừng và đóng cửa sổ Profile",
        )
        logger.info("[Tiến trình theo dõi] Đã gửi lệnh dừng Profile %s.", profile_id)
        if close_browsers:
            self._close_profile_browsers_async(profile_id)
        return snapshot

    def start_all(self) -> dict:
        profiles = config.load_profile_configs()
        started = []
        for profile_id, profile in profiles.items():
            if not bool(profile.get("enabled", True)) or not get_tracking_sources(profile):
                continue
            before = self._states.get(str(profile_id), {}).get("status")
            state = self.start_profile(str(profile_id))
            if before not in {"starting", "running", "degraded", "stopping"} and state["active"]:
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

    def _run_profile(
        self,
        profile_id: str,
        stop_event: threading.Event,
        startup_delay_seconds: float = 0,
    ) -> None:
        failed = False
        watcher_session = None
        try:
            if startup_delay_seconds > 0:
                self._set_state(
                    profile_id,
                    status="starting",
                    message=f"Chờ {startup_delay_seconds:.0f} giây để quét lệch CPU",
                )
                if stop_event.wait(startup_delay_seconds):
                    return
            profile = self._load_profile(profile_id)
            sources = get_tracking_sources(profile)
            if not sources:
                raise RuntimeError("Profile không có nguồn theo dõi nào đang bật.")

            from application.workflows.profile_worker import (
                ProfileWorker,
                check_douyin_direct_download,
            )

            worker = ProfileWorker(profile)
            is_mock_worker = type(worker).__name__ in ("Mock", "MagicMock")
            if not is_mock_worker:
                watcher_session = PersistentWatcherSession(profile_id, profile, self._api_url())

            if any(source.get("platform") == "douyin" for source in sources):
                gemlogin_id = str(
                    (profile.get("douyin", {}) or {}).get("gemlogin_profile_id") or profile_id
                )
                watcher_page = (
                    watcher_session.acquire_page(platform="douyin")
                    if watcher_session is not None
                    else None
                )
                result = check_douyin_direct_download(
                    gemlogin_id,
                    api_url=self._api_url(),
                    profile_config=profile,
                    page=watcher_page,
                )
                if stop_event.is_set():
                    return
                if not result.get("ok"):
                    raise RuntimeError(str(result.get("message") or "Preflight thất bại."))
            self._set_state(
                profile_id,
                status="starting",
                message=f"Đang kiểm tra lần đầu {len(sources)} nguồn",
                source_count=len(sources),
                last_error="",
            )
            self._monitor_loop(profile_id, stop_event, watcher_session=watcher_session)
        except Exception as exc:
            failed = True
            error = str(exc)
            logger.exception("[Tiến trình theo dõi] Profile %s dừng do lỗi: %s", profile_id, exc)
            self._set_state(
                profile_id,
                status="error",
                message="Tracking gặp lỗi",
                last_error=error,
                current_source="",
            )
            send_error_notification(
                f"Không thể chạy Tracking: {error}",
                profile_id=profile_id,
            )
            self._close_profile_browsers_async(profile_id)
        finally:
            if failed and watcher_session is not None:
                try:
                    watcher_session.close()
                except Exception:
                    pass
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
                logger.info("[Tiến trình theo dõi] Luồng theo dõi Profile %s đã kết thúc.", profile_id)

    def _record_source_scan(
        self,
        profile_id: str,
        source: dict,
        source_count: int,
        *,
        succeeded: bool,
        error: str = "",
        source_label: str = "",
    ) -> dict:
        """Persist runtime health for one source and derive the Profile status."""
        profile_id = str(profile_id)
        source_key = str(source.get("source_key") or "")
        timestamp = _now()
        with self._lock:
            state = self._states.setdefault(profile_id, {})
            if str(state.get("status")) == "stopping":
                return self._public_state(profile_id, state)
            health = dict(state.get("source_health") or {})
            item = dict(health.get(source_key) or {})
            item.update(
                {
                    "source_key": source_key,
                    "platform": str(source.get("platform") or ""),
                    "label": str(source_label or tracking_source_label(source)),
                    "last_attempt_at": timestamp,
                    "attempt_count": int(item.get("attempt_count") or 0) + 1,
                }
            )
            if succeeded:
                item["last_success_at"] = timestamp
                item["consecutive_failures"] = 0
                item["last_error"] = ""
            else:
                item["consecutive_failures"] = int(item.get("consecutive_failures") or 0) + 1
                item["last_error"] = str(error or "Không lấy được dữ liệu hợp lệ từ nguồn.")
            health[source_key] = item

            successful_count = sum(bool(row.get("last_success_at")) for row in health.values())
            failing_count = sum(
                int(row.get("consecutive_failures") or 0) > 0
                for row in health.values()
            )
            current_error = next(
                (
                    str(row.get("last_error") or "")
                    for row in health.values()
                    if int(row.get("consecutive_failures") or 0) > 0
                ),
                "",
            )
            attempted_count = sum(int(row.get("attempt_count") or 0) > 0 for row in health.values())
            if successful_count == 0:
                status = "degraded" if attempted_count >= source_count else "starting"
            elif failing_count:
                status = "degraded"
            else:
                status = "running"

            if status == "starting":
                message = f"Đang kiểm tra lần đầu {source_count} nguồn"
            elif status == "degraded":
                message = f"Đang chạy, {failing_count}/{source_count} nguồn đang lỗi"
            else:
                message = f"Đang giám sát {source_count} nguồn"
            state.update(
                {
                    "status": status,
                    "message": message,
                    "source_count": source_count,
                    "source_health": health,
                    "last_error": current_error,
                    "updated_at": timestamp,
                }
            )
            snapshot = self._public_state(profile_id, state)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                pass
        return snapshot

    @staticmethod
    def _collect_source_videos(profile: dict, worker, monitor, page=None) -> tuple[list, bool]:
        """Create a baseline once, otherwise compare against persisted state."""
        if monitor.state.path.exists():
            if page is not None:
                try:
                    videos = monitor.get_new_videos(page=page)
                except TypeError:
                    videos = monitor.get_new_videos()
            else:
                videos = monitor.get_new_videos()
            succeeded = bool(getattr(monitor, "last_scan_succeeded", False))
            if succeeded:
                monitor.state.touch()
            return videos, succeeded

        initial_scan_mode = str(
            profile.get("initial_scan_mode") or "skip_existing"
        ).strip().casefold()
        if initial_scan_mode != "process_latest":
            if page is not None:
                try:
                    baseline_videos = monitor.build_start_baseline(page=page)
                except TypeError:
                    baseline_videos = monitor.build_start_baseline()
            else:
                baseline_videos = monitor.build_start_baseline()
            succeeded = bool(getattr(monitor, "last_scan_succeeded", False))
            return [], succeeded and baseline_videos is not None

        if page is not None:
            try:
                initial_videos = monitor.fetch_latest_videos(pages_to_fetch=1, page=page)
            except TypeError:
                initial_videos = monitor.fetch_latest_videos(pages_to_fetch=1)
        else:
            initial_videos = monitor.fetch_latest_videos(pages_to_fetch=1)

        succeeded = bool(getattr(monitor, "last_scan_succeeded", False))
        if not succeeded:
            return [], False
        eligible_videos = [
            video
            for video in initial_videos
            if video.like_count >= worker.min_likes
            and video.duration_seconds <= worker.max_duration
        ]
        selected_video = max(
            eligible_videos,
            key=lambda video: video.create_time,
            default=None,
        )
        baseline_videos = [
            video
            for video in initial_videos
            if selected_video is None or video.aweme_id != selected_video.aweme_id
        ]
        monitor.state.replace_seen(
            [video.aweme_id for video in baseline_videos],
            max((video.create_time for video in initial_videos), default=0),
        )
        return ([selected_video] if selected_video else []), True

    def _monitor_loop(
        self,
        profile_id: str,
        stop_event: threading.Event,
        *,
        watcher_session: PersistentWatcherSession | None = None,
    ) -> None:
        from application.workflows.profile_worker import (
            ProfileWorker,
            enabled_platform_names,
        )
        from services.integrations.telegram_service import send_new_video_notification

        next_scan_at: dict[str, float] = {}
        source_states: dict[str, object] = {}
        processing_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"process-profile-{profile_id}",
        )
        processing_futures: dict[tuple[str, str], Future] = {}
        profile = self._load_profile(profile_id)

        try:
            while not stop_event.is_set():
                for job_key, future in list(processing_futures.items()):
                    if not future.done():
                        continue
                    processing_futures.pop(job_key, None)
                    try:
                        future.result()
                    except Exception as exc:
                        logger.exception(
                            "[Tiến trình theo dõi] Luồng xử lý video của Profile %s gặp lỗi: %s",
                            profile_id,
                            exc,
                        )

                profile = self._load_profile(profile_id)
                sources = get_tracking_sources(profile)
                if not sources:
                    raise RuntimeError("Profile không có nguồn theo dõi nào đang bật.")

                now = time.time()
                active_keys = {source["source_key"] for source in sources}
                next_scan_at = {key: value for key, value in next_scan_at.items() if key in active_keys}
                source_states = {
                    key: value for key, value in source_states.items() if key in active_keys
                }
                for source in sources:
                    next_scan_at.setdefault(source["source_key"], now)
                due_sources = [
                    source for source in sources
                    if next_scan_at.get(source["source_key"], 0) <= now
                ]
                if not due_sources:
                    with self._lock:
                        current_status = str(
                            self._states.get(profile_id, {}).get("status") or "starting"
                        )
                    self._set_state(profile_id, status=current_status, current_source="")
                    stop_event.wait(1)
                    continue

                worker = ProfileWorker(profile)
                is_mock_worker = type(worker).__name__ in ("Mock", "MagicMock")
                if not is_mock_worker and watcher_session is None:
                    watcher_session = PersistentWatcherSession(profile_id, profile, self._api_url())

                total_sources = len(sources)
                source_orders = {
                    str(s.get("source_key")): idx
                    for idx, s in enumerate(sources, start=1)
                }

                for index, source in enumerate(due_sources):
                    if stop_event.is_set():
                        break
                    if index > 0:
                        if stop_event.wait(3.0):
                            break
                    source_key = source["source_key"]
                    order = source_orders.get(str(source_key), index + 1)
                    try:
                        source_label = worker._source_label(
                            source, index=order, total=total_sources
                        )
                    except TypeError:
                        source_label = worker._source_label(source)
                    if not source_label or str(source_label).startswith("..."):
                        display_name = str(source.get("display_name") or "").strip()
                        source_label = (
                            f"nguồn {order}/{total_sources} ({display_name})"
                            if display_name
                            else f"nguồn {order}/{total_sources}"
                        )
                    label_text = (
                        str(source_label)
                        if str(source_label).startswith("nguồn")
                        else f"nguồn {source_label}"
                    )
                    with self._lock:
                        current_status = str(
                            self._states.get(profile_id, {}).get("status") or "starting"
                        )
                    self._set_state(
                        profile_id,
                        status=current_status,
                        message=f"Đang quét {label_text}",
                        current_source=source_label,
                        source_count=len(sources),
                    )
                    logger.info("[Tiến trình theo dõi] Profile %s đang quét %s.", profile_id, label_text)
                    source_platform = str(source.get("platform") or "douyin").strip().casefold()
                    watcher_page = (
                        watcher_session.acquire_page(platform=source_platform)
                        if watcher_session is not None
                        else None
                    )
                    try:
                        monitor = worker._create_monitor(source)
                        shared_state = source_states.setdefault(source_key, monitor.state)
                        monitor.state = shared_state
                        pending_videos = worker.get_pending_videos(source)
                        state_existed = monitor.state.path.exists()
                        detected_videos, scan_succeeded = self._collect_source_videos(
                            profile,
                            worker,
                            monitor,
                            page=watcher_page,
                        )
                        if scan_succeeded and not state_existed:
                            if str(profile.get("initial_scan_mode") or "skip_existing").casefold() == "process_latest":
                                logger.info(
                                    "[Tiến trình theo dõi] Profile %s tạo mốc ban đầu cho %s và chọn %s video hiện có để xử lý.",
                                    profile_id,
                                    label_text,
                                    len(detected_videos),
                                )
                            else:
                                logger.info(
                                    "[Tiến trình theo dõi] Profile %s đã tạo mốc ban đầu cho %s.",
                                    profile_id,
                                    label_text,
                                )
                        self._record_source_scan(
                            profile_id,
                            source,
                            len(sources),
                            succeeded=scan_succeeded,
                            error="" if scan_succeeded else "Không lấy được dữ liệu hợp lệ từ nguồn.",
                            source_label=source_label,
                        )
                        if not scan_succeeded and not pending_videos:
                            continue

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
                            # Persist the job before notifying Telegram.  The
                            # notification is only useful if the desktop queue
                            # already contains the same video; sending it first
                            # can leave a misleading "new video" alert when
                            # registration fails or the job is immediately
                            # dismissed by stale state.
                            worker.register_video(video, source)
                            send_new_video_notification(
                                profile_id,
                                worker.name,
                                source_label,
                                video,
                                enabled_platform_names(
                                    profile,
                                    source.get("platform", "douyin"),
                                ),
                            )

                        for video in pending_videos + new_videos:
                            if stop_event.is_set():
                                break
                            job_key = (source_key, str(video.aweme_id))
                            existing = processing_futures.get(job_key)
                            if existing is not None and not existing.done():
                                continue
                            processing_futures[job_key] = processing_executor.submit(
                                worker.process_video,
                                video,
                                source,
                                monitor,
                                stop_event=stop_event,
                            )
                            logger.info(
                                "[Tiến trình theo dõi] Profile %s đã xếp video %s vào hàng đợi xử lý nền.",
                                profile_id,
                                video.aweme_id,
                            )
                    except Exception as exc:
                        logger.exception(
                            "[Tiến trình theo dõi] Lỗi %s của Profile %s: %s",
                            label_text,
                            profile_id,
                            exc,
                        )
                        if is_browser_connection_error(exc, profile):
                            watcher_session.close()
                        send_error_notification(
                            f"Lỗi {label_text}: {exc}",
                            profile_id=profile_id,
                        )
                        self._record_source_scan(
                            profile_id,
                            source,
                            len(sources),
                            succeeded=False,
                            error=str(exc),
                            source_label=source_label,
                        )
                    finally:
                        scan_jitter = self._jitter_seconds(
                            SCAN_JITTER_MIN_SECONDS,
                            SCAN_JITTER_MAX_SECONDS,
                        )
                        next_scan_at[source_key] = (
                            time.time()
                            + source["check_interval_minutes"] * 60
                            + scan_jitter
                        )
                        logger.info(
                            "[Tiến trình theo dõi] Profile %s sẽ quét lại %s sau khoảng %s phút %.0f giây.",
                            profile_id,
                            label_text,
                            source["check_interval_minutes"],
                            scan_jitter,
                        )

                if not stop_event.is_set():
                    stop_event.wait(1)
        finally:
            stop_event.set()
            if watcher_session is not None:
                watcher_session.close()
            processing_executor.shutdown(wait=True, cancel_futures=True)

    def _close_profile_browsers_async(self, profile_id: str) -> None:
        threading.Thread(
            target=self._close_profile_browsers,
            args=(str(profile_id),),
            daemon=True,
            name=f"close-profile-{profile_id}",
        ).start()

    def _close_profile_browsers(self, profile_id: str) -> None:
        gemlogin_ids = {str(profile_id)}
        profile: dict = {}
        try:
            profile = self._load_profile(profile_id)
            if browser_provider(profile) == "local_chromium":
                gemlogin_ids = {
                    str((profile.get("douyin") or {}).get("gemlogin_profile_id") or profile_id)
                }
            else:
                for section in ("douyin", "tiktok", "facebook", "youtube"):
                    configured_id = str(
                        (profile.get(section, {}) or {}).get("gemlogin_profile_id") or ""
                    ).strip()
                    if configured_id:
                        gemlogin_ids.add(configured_id)
        except Exception as exc:
            logger.warning("Không đọc được cấu hình trình duyệt của Profile %s: %s", profile_id, exc)

        for gemlogin_id in sorted(gemlogin_ids):
            try:
                close_gemlogin_profile(
                    gemlogin_id,
                    self._api_url(),
                    profile_config=profile,
                )
                logger.info("[Tiến trình theo dõi] Đã đóng trình duyệt Profile %s.", profile_id)
            except Exception as exc:
                logger.warning("Không thể đóng trình duyệt Profile %s: %s", profile_id, exc)
