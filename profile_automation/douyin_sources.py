import hashlib
from typing import Iterable


DEFAULT_SOURCE_INTERVAL_MINUTES = 30
MAX_NEW_VIDEOS_PER_SOURCE = 1


def normalize_sec_uid(value) -> str:
    return str(value or "").strip()


def source_key(sec_uid: str) -> str:
    normalized = normalize_sec_uid(sec_uid)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def _normalize_interval(value, fallback: int) -> int:
    try:
        interval = int(value)
    except (TypeError, ValueError):
        interval = fallback
    return interval if interval > 0 else fallback


def get_douyin_sources(profile_config: dict, include_disabled: bool = False) -> list[dict]:
    """Return normalized Douyin sources while supporting the legacy single-source config."""
    douyin_cfg = profile_config.get("douyin", {}) or {}
    legacy_sec_uid = normalize_sec_uid(douyin_cfg.get("target_sec_uid"))
    legacy_state_sec_uid = normalize_sec_uid(
        douyin_cfg.get("legacy_state_sec_uid", legacy_sec_uid)
    )
    raw_sources = douyin_cfg.get("sources")

    if not isinstance(raw_sources, list):
        raw_sources = []
    legacy_source_interval = (
        raw_sources[0].get("check_interval_minutes")
        if raw_sources and isinstance(raw_sources[0], dict)
        else DEFAULT_SOURCE_INTERVAL_MINUTES
    )
    common_interval = _normalize_interval(
        profile_config.get("check_interval_minutes"),
        _normalize_interval(legacy_source_interval, DEFAULT_SOURCE_INTERVAL_MINUTES),
    )
    if not raw_sources and legacy_sec_uid:
        raw_sources = [
            {
                "target_sec_uid": legacy_sec_uid,
                "target_display_name": douyin_cfg.get("target_display_name", ""),
                "check_interval_minutes": common_interval,
                "enabled": True,
            }
        ]

    normalized_sources = []
    seen_uids = set()
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            continue
        sec_uid = normalize_sec_uid(raw_source.get("target_sec_uid"))
        if not sec_uid or sec_uid in seen_uids:
            continue
        seen_uids.add(sec_uid)

        enabled = bool(raw_source.get("enabled", True))
        if not include_disabled and not enabled:
            continue

        normalized_sources.append(
            {
                "source_key": source_key(sec_uid),
                "target_sec_uid": sec_uid,
                "target_display_name": str(raw_source.get("target_display_name") or "").strip(),
                "check_interval_minutes": common_interval,
                "enabled": enabled,
                "migrate_legacy_state": sec_uid == legacy_state_sec_uid,
            }
        )

    return normalized_sources


def serialize_douyin_sources(sources: Iterable[dict]) -> list[dict]:
    serialized = []
    for source in sources:
        sec_uid = normalize_sec_uid(source.get("target_sec_uid"))
        if not sec_uid:
            continue
        serialized.append(
            {
                "target_sec_uid": sec_uid,
                "target_display_name": str(source.get("target_display_name") or "").strip(),
                "enabled": bool(source.get("enabled", True)),
            }
        )
    return serialized
