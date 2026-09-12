from __future__ import annotations

import heapq
import itertools
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

import core.config as config
from core.utils import logger


RESOURCE_SETTING_KEYS = {
    "download": "MAX_CONCURRENT_DOWNLOADS",
    "ffmpeg": "MAX_CONCURRENT_FFMPEG",
    "upload": "MAX_CONCURRENT_UPLOADS",
}
RESOURCE_DEFAULT_LIMITS = {"download": 2, "ffmpeg": 1, "upload": 2}
RESOURCE_LOG_LABELS = {
    "download": "tải video",
    "ffmpeg": "chuyển đổi video",
    "upload": "đăng video",
}


class WorkloadCancelled(RuntimeError):
    pass


@dataclass(order=True)
class _Waiter:
    priority: int
    sequence: int
    profile_id: str = field(compare=False)
    video_id: str = field(compare=False)
    platform: str = field(compare=False, default="")
    queued_at: float = field(compare=False, default_factory=time.monotonic)
    cancelled: bool = field(compare=False, default=False)


class PriorityResourcePool:
    def __init__(self, name: str, default_limit: int):
        self.name = str(name)
        self.default_limit = max(1, int(default_limit))
        self._condition = threading.Condition(threading.RLock())
        self._sequence = itertools.count()
        self._waiters: list[_Waiter] = []
        self._active: list[dict] = []

    def _limit(self) -> int:
        key = RESOURCE_SETTING_KEYS[self.name]
        try:
            value = config.load_settings().get(key, self.default_limit)
            return max(1, min(32, int(value)))
        except (TypeError, ValueError):
            return self.default_limit

    def acquire(
        self,
        *,
        profile_id: str,
        video_id: str,
        platform: str = "",
        priority: int = 100,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        waiter = _Waiter(
            priority=int(priority),
            sequence=next(self._sequence),
            profile_id=str(profile_id or ""),
            video_id=str(video_id or ""),
            platform=str(platform or ""),
        )
        announced = False
        with self._condition:
            heapq.heappush(self._waiters, waiter)
            while True:
                while self._waiters and self._waiters[0].cancelled:
                    heapq.heappop(self._waiters)
                if cancel_event is not None and cancel_event.is_set():
                    waiter.cancelled = True
                    self._condition.notify_all()
                    raise WorkloadCancelled(
                        f"Profile {waiter.profile_id} đã dừng khi đang chờ tài nguyên {self.name}."
                    )
                is_next = bool(self._waiters and self._waiters[0] is waiter)
                if is_next and len(self._active) < self._limit():
                    heapq.heappop(self._waiters)
                    token = {
                        "resource": self.name,
                        "profile_id": waiter.profile_id,
                        "video_id": waiter.video_id,
                        "platform": waiter.platform,
                        "priority": waiter.priority,
                        "started_at_monotonic": time.monotonic(),
                    }
                    self._active.append(token)
                    self._condition.notify_all()
                    return token
                if not announced:
                    announced = True
                    logger.info(
                        "[Điều phối] Profile %s, video %s đang chờ tài nguyên %s (ưu tiên=%s).",
                        waiter.profile_id,
                        waiter.video_id,
                        RESOURCE_LOG_LABELS.get(self.name, self.name),
                        waiter.priority,
                    )
                self._condition.wait(timeout=0.5)

    def release(self, token: dict) -> None:
        with self._condition:
            if token in self._active:
                self._active.remove(token)
            self._condition.notify_all()

    def snapshot(self) -> dict:
        with self._condition:
            waiters = sorted(
                (waiter for waiter in self._waiters if not waiter.cancelled),
                key=lambda item: (item.priority, item.sequence),
            )
            now = time.monotonic()
            return {
                "limit": self._limit(),
                "active_count": len(self._active),
                "waiting_count": len(waiters),
                "active": [
                    {
                        key: value
                        for key, value in item.items()
                        if key != "started_at_monotonic"
                    }
                    | {"elapsed_seconds": round(now - item["started_at_monotonic"], 1)}
                    for item in self._active
                ],
                "waiting": [
                    {
                        "profile_id": item.profile_id,
                        "video_id": item.video_id,
                        "platform": item.platform,
                        "priority": item.priority,
                        "waiting_seconds": round(now - item.queued_at, 1),
                    }
                    for item in waiters
                ],
            }


_POOLS = {
    name: PriorityResourcePool(name, RESOURCE_DEFAULT_LIMITS[name])
    for name in RESOURCE_DEFAULT_LIMITS
}


def priority_for_job(job: dict | None, profile: dict | None = None) -> int:
    """Lower values run first: partial/resumed uploads, downloaded jobs, then new work."""
    job = job or {}
    profile = profile or {}
    try:
        configured = int(profile.get("processing_priority", 100))
    except (TypeError, ValueError):
        configured = 100
    configured = max(0, min(1000, configured))

    platforms = job.get("platforms", {}) or {}
    if any((state or {}).get("status") == "success" for state in platforms.values()):
        return max(0, configured - 40)
    if str(job.get("download_path") or "") or job.get("status") == "downloaded":
        return max(0, configured - 25)
    attempts = job.get("attempts", {}) or {}
    if any(int(value or 0) > 0 for value in attempts.values()):
        return max(0, configured - 10)
    return configured


@contextmanager
def workload_slot(
    resource: str,
    *,
    profile_id: str,
    video_id: str,
    platform: str = "",
    priority: int = 100,
    cancel_event: threading.Event | None = None,
) -> Iterator[dict]:
    pool = _POOLS[str(resource)]
    token = pool.acquire(
        profile_id=profile_id,
        video_id=video_id,
        platform=platform,
        priority=priority,
        cancel_event=cancel_event,
    )
    try:
        yield token
    finally:
        pool.release(token)


def workload_snapshot() -> dict:
    return {name: pool.snapshot() for name, pool in _POOLS.items()}
