from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

import core.config as config
from application.workflows.video_job_store import TERMINAL_STATUSES, VideoJobStore
from services.runtime.activity_history_service import ActivityHistoryStore
from services.browser.browser_profile_service import get_browser_profile_health


PROCESSING_STATUSES = {"downloading", "uploading"}
WAITING_STATUSES = {"detected", "waiting_caption", "caption_ready", "downloaded"}
PLATFORM_LABELS = {
    "tiktok": "TikTok",
    "facebook": "Facebook Reels",
    "youtube": "YouTube Shorts",
}
RESTRICTION_TERMS = ("hạn chế", "han che", "bản quyền", "ban quyen", "copyright", "restricted", "restriction")


def _parse_datetime(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None


def _day_key(value: object) -> str:
    parsed = _parse_datetime(value)
    return parsed.strftime("%Y-%m-%d") if parsed else ""


def _job_key(profile_id: object, video_id: object) -> tuple[str, str]:
    return str(profile_id or ""), str(video_id or "")


def _active_profile_ids(active_profile_ids: Iterable[str] | None) -> set[str]:
    return {str(profile_id) for profile_id in (active_profile_ids or ())}


def _failure_bucket(status: object, error: object = "") -> str:
    text = f"{status or ''} {error or ''}".casefold()
    return "restricted" if str(status or "") == "skipped" or any(term in text for term in RESTRICTION_TERMS) else "publish_error"


def _job_failure_bucket(job: dict) -> str:
    states = [state for state in (job.get("platforms") or {}).values() if isinstance(state, dict) and state.get("status") in {"failed", "skipped"}]
    if states and all(_failure_bucket(state.get("status"), state.get("last_error")) == "restricted" for state in states):
        return "restricted"
    return _failure_bucket(job.get("status"), job.get("last_error"))


def build_overview_snapshot(
    *,
    job_store: VideoJobStore | None = None,
    history_store: ActivityHistoryStore | None = None,
    profiles: dict | None = None,
    active_profile_ids: Iterable[str] | None = None,
    now: datetime | None = None,
) -> dict:
    current_time = now or datetime.now()
    store = job_store or VideoJobStore()
    history = history_store
    if history is None:
        try:
            history = ActivityHistoryStore()
        except Exception:
            history = None
    profile_configs = profiles if profiles is not None else config.load_profile_configs()
    jobs = store.list_jobs()
    active_ids = _active_profile_ids(active_profile_ids)

    day_keys = [
        (current_time - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(6, -1, -1)
    ]
    daily_sets = {
        day: {"detected": set(), "completed": set(), "restricted": set(), "publish_error": set()}
        for day in day_keys
    }

    since = (current_time - timedelta(days=7)).isoformat(timespec="seconds")
    try:
        events = history.list_events_since(since) if history is not None else []
    except Exception:
        events = []

    for event in events:
        day = _day_key(event.get("occurred_at"))
        if day not in daily_sets:
            continue
        key = _job_key(event.get("profile_id"), event.get("video_id"))
        event_type = str(event.get("event_type") or "")
        status = str(event.get("status") or "")
        if event_type == "detected":
            daily_sets[day]["detected"].add(key)
        if event_type == "status" and status == "completed":
            daily_sets[day]["completed"].add(key)
        if (event_type == "status" and status.startswith("failed")) or (event_type == "platform" and status in {"failed", "skipped"}):
            daily_sets[day][_failure_bucket(status, event.get("error"))].add(key)

    for job in jobs:
        key = _job_key(job.get("profile_id"), job.get("video_id"))
        created_day = _day_key(job.get("created_at"))
        if created_day in daily_sets:
            daily_sets[created_day]["detected"].add(key)
        updated_day = _day_key(job.get("updated_at"))
        status = str(job.get("status") or "")
        if updated_day in daily_sets and status == "completed":
            daily_sets[updated_day]["completed"].add(key)
        if updated_day in daily_sets and status.startswith("failed"):
            daily_sets[updated_day][_job_failure_bucket(job)].add(key)

    daily = []
    day_names = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
    for day in day_keys:
        date_value = datetime.strptime(day, "%Y-%m-%d")
        daily.append(
            {
                "date": day,
                "label": day_names[date_value.weekday()],
                "detected": len(daily_sets[day]["detected"]),
                "completed": len(daily_sets[day]["completed"]),
                "restricted": len(daily_sets[day]["restricted"]),
                "publish_error": len(daily_sets[day]["publish_error"]),
            }
        )

    today = current_time.strftime("%Y-%m-%d")
    today_data = daily_sets[today]
    completed_today = len(today_data["completed"])
    unresolved_failed_today = len((today_data["restricted"] | today_data["publish_error"]) - today_data["completed"])
    outcomes_today = completed_today + unresolved_failed_today
    failed_jobs = [job for job in jobs if str(job.get("status") or "").startswith("failed")]
    restricted_jobs = [job for job in failed_jobs if _job_failure_bucket(job) == "restricted"]
    current_jobs = [job for job in jobs if job.get("status") not in TERMINAL_STATUSES]

    kpis = {
        "detected_today": len(today_data["detected"]),
        "completed_today": completed_today,
        "waiting": sum(
            str(job.get("status") or "") in WAITING_STATUSES | PROCESSING_STATUSES
            for job in current_jobs
        ),
        "restricted": len(restricted_jobs),
        "publish_errors": len(failed_jobs) - len(restricted_jobs),
        "errors": len(failed_jobs),
        "success_rate": round(completed_today * 100 / outcomes_today) if outcomes_today else 0,
    }

    platform_stats = []
    for platform, label in PLATFORM_LABELS.items():
        states = [
            job.get("platforms", {}).get(platform, {})
            for job in jobs
            if platform in set(job.get("enabled_platforms") or ())
        ]
        success = sum(state.get("status") == "success" for state in states)
        failed = sum(state.get("status") == "failed" for state in states)
        pending = sum(state.get("status") in {"pending", "uploading"} for state in states)
        outcomes = success + failed
        platform_stats.append(
            {
                "key": platform,
                "label": label,
                "success": success,
                "failed": failed,
                "pending": pending,
                "rate": round(success * 100 / outcomes) if outcomes else 0,
            }
        )

    jobs_by_profile: dict[str, list[dict]] = {}
    for job in jobs:
        jobs_by_profile.setdefault(str(job.get("profile_id") or ""), []).append(job)

    profile_rows = []
    for profile_id, profile in sorted(
        profile_configs.items(), key=lambda item: str(item[0]).zfill(8)
    ):
        profile_id = str(profile_id)
        profile_jobs = jobs_by_profile.get(profile_id, [])
        douyin_cfg = profile.get("douyin", {}) or {}
        gemlogin_id = str(douyin_cfg.get("gemlogin_profile_id") or profile_id)
        health = get_browser_profile_health(gemlogin_id, profile)
        active_job = next((job for job in profile_jobs if job.get("active")), None)
        latest_update = max(
            (str(job.get("updated_at") or "") for job in profile_jobs),
            default="",
        )
        profile_rows.append(
            {
                "profile_id": profile_id,
                "name": str(profile.get("name") or f"Profile {profile_id}"),
                "enabled": bool(profile.get("enabled", True)),
                "running": profile_id in active_ids or active_job is not None,
                "health": str(health.get("status") or "unknown"),
                "recovery_count": int(health.get("recovery_count") or 0),
                "queue": sum(
                    job.get("status") not in TERMINAL_STATUSES
                    and not str(job.get("status") or "").startswith("failed")
                    for job in profile_jobs
                ),
                "errors": sum(
                    str(job.get("status") or "").startswith("failed")
                    for job in profile_jobs
                ),
                "current_video": str((active_job or {}).get("video_id") or ""),
                "last_activity": latest_update,
            }
        )

    failed_jobs.sort(key=lambda job: str(job.get("updated_at") or ""), reverse=True)
    recent_errors = [
        {
            "profile_id": str(job.get("profile_id") or ""),
            "video_id": str(job.get("video_id") or ""),
            "status": str(job.get("status") or "failed"),
            "error": str(job.get("last_error") or ""),
            "updated_at": str(job.get("updated_at") or ""),
        }
        for job in failed_jobs[:8]
    ]

    recent_activity = [
        {
            "occurred_at": str(event.get("occurred_at") or ""),
            "profile_id": str(event.get("profile_id") or ""),
            "video_id": str(event.get("video_id") or ""),
            "event_type": str(event.get("event_type") or ""),
            "status": str(event.get("status") or ""),
            "platform": str(event.get("platform") or ""),
            "error": str(event.get("error") or ""),
        }
        for event in reversed(events)
        if str(event.get("event_type") or "") in {"platform", "status", "detected"}
    ][:6]

    return {
        "generated_at": current_time.isoformat(timespec="seconds"),
        "kpis": kpis,
        "daily": daily,
        "platforms": platform_stats,
        "profiles": profile_rows,
        "errors": recent_errors,
        "recent_activity": recent_activity,
    }
