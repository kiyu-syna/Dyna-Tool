from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import core.config as config
from core.runtime_paths import logs_dir, profile_state_dir
from core.utils import logger
from application.tracking.sources import get_tracking_sources
from application.workflows.video_job_store import VideoJobStore
from services.runtime.activity_history_service import ActivityHistoryStore


STALE_TEMP_FILE_DAYS = 1
SCREENSHOT_RETENTION_DAYS = 3
ORPHAN_SOURCE_STATE_RETENTION_DAYS = 30
OLD_LOG_BACKUP_RETENTION_DAYS = 30


def _delete_if_older(path: Path, cutoff_timestamp: float) -> bool:
    try:
        if path.is_file() and path.stat().st_mtime < cutoff_timestamp:
            path.unlink()
            return True
    except OSError:
        pass
    return False


def _clean_matching_files(
    root: Path,
    patterns: tuple[str, ...],
    cutoff: float,
    *,
    recursive: bool = True,
) -> int:
    if not root.exists():
        return 0
    removed = 0
    for pattern in patterns:
        paths = root.rglob(pattern) if recursive else root.glob(pattern)
        for path in paths:
            removed += int(_delete_if_older(path, cutoff))
    return removed


def _expected_source_state_names(base_dir: Path) -> set[str]:
    expected = set()
    for profile_id, profile in config.load_profile_configs().items():
        for source in get_tracking_sources(profile, include_disabled=True):
            expected.add(
                f"profile_{profile_id}_source_{source['source_key']}_seen.json"
            )
    return expected


def _clean_orphan_source_states(base_dir: Path, cutoff: float) -> int:
    state_dir = profile_state_dir(base_dir)
    if not state_dir.exists():
        return 0
    expected = _expected_source_state_names(base_dir)
    removed = 0
    for path in state_dir.glob("profile_*_source_*_seen.json"):
        if path.name not in expected:
            removed += int(_delete_if_older(path, cutoff))
    return removed


def run_runtime_maintenance(
    *,
    base_dir: str | Path | None = None,
    include_user_video_dir: bool = True,
    now: datetime | None = None,
) -> dict:
    current_time = now or datetime.now()
    root = Path(base_dir or config.BASE_DIR)
    summary = {
        "terminal_jobs": 0,
        "history_events": 0,
        "temporary_files": 0,
        "screenshots": 0,
        "orphan_source_states": 0,
        "old_log_backups": 0,
    }

    try:
        summary["terminal_jobs"] = VideoJobStore().prune_terminal_jobs(now=current_time)
    except Exception as exc:
        logger.warning("Không thể dọn hàng đợi terminal cũ: %s", exc)
    try:
        summary["history_events"] = ActivityHistoryStore().prune(
            retention_days=90,
            now=current_time,
        )
    except Exception as exc:
        logger.warning("Không thể dọn lịch sử thống kê cũ: %s", exc)
    temp_cutoff = (current_time - timedelta(days=STALE_TEMP_FILE_DAYS)).timestamp()
    screenshot_cutoff = (
        current_time - timedelta(days=SCREENSHOT_RETENTION_DAYS)
    ).timestamp()
    log_cutoff = (
        current_time - timedelta(days=OLD_LOG_BACKUP_RETENTION_DAYS)
    ).timestamp()
    orphan_cutoff = (
        current_time - timedelta(days=ORPHAN_SOURCE_STATE_RETENTION_DAYS)
    ).timestamp()

    cleanup_roots = [root / "Downloads"]
    if include_user_video_dir:
        cleanup_roots.append(Path.home() / "Videos" / "Tracking Douyin")
        cleanup_roots.append(Path.home() / "Videos" / "Tracking TikTok")
    for cleanup_root in cleanup_roots:
        summary["temporary_files"] += _clean_matching_files(
            cleanup_root,
            ("*.part", "*_youtube_shorts*.mp4"),
            temp_cutoff,
        )
    summary["screenshots"] = _clean_matching_files(
        root,
        ("intervention_*.png", "error_screenshot*.png"),
        screenshot_cutoff,
        recursive=False,
    )
    summary["old_log_backups"] = sum(
        _clean_matching_files(
            log_root,
            ("system.log.*",),
            log_cutoff,
            recursive=False,
        )
        for log_root in (
            logs_dir(root),
            root / "logs",
            root / "core" / "logs",
        )
    )
    summary["orphan_source_states"] = _clean_orphan_source_states(root, orphan_cutoff)
    summary["total_removed"] = sum(int(value) for value in summary.values())
    logger.info(
        "[Bảo trì] Đã dọn %s mục dữ liệu cũ.",
        int(summary.get("total_removed") or 0),
    )
    return summary
