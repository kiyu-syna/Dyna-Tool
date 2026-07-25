import hashlib
from typing import Iterable
from urllib.parse import unquote, urlparse


DEFAULT_SOURCE_INTERVAL_MINUTES = 30
MAX_NEW_VIDEOS_PER_SOURCE = 1
SUPPORTED_SOURCE_PLATFORMS = ("douyin", "tiktok")


def _interval(value, fallback: int = DEFAULT_SOURCE_INTERVAL_MINUTES) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = fallback
    return result if result > 0 else fallback


def normalize_tiktok_unique_id(value) -> str:
    raw = unquote(str(value or "").strip())
    if not raw:
        return ""
    if "://" in raw:
        path = urlparse(raw).path
        raw = next((part for part in path.split("/") if part.startswith("@")), "")
    return raw.strip().lstrip("@").split("?")[0].split("/")[0].strip()


def tracking_source_key(platform: str, identity: str) -> str:
    normalized_platform = str(platform or "").strip().casefold()
    raw_identity = str(identity or "").strip()
    if normalized_platform == "douyin":
        return hashlib.sha1(raw_identity.encode("utf-8")).hexdigest()[:12]
    normalized_identity = raw_identity.casefold()
    digest = hashlib.sha1(
        f"{normalized_platform}:{normalized_identity}".encode("utf-8")
    ).hexdigest()[:12]
    return f"{normalized_platform}_{digest}"


def _normalize_tracking_source(raw: dict, common_interval: int) -> dict | None:
    platform = str(raw.get("platform") or "").strip().casefold()
    if platform not in SUPPORTED_SOURCE_PLATFORMS:
        return None

    enabled = bool(raw.get("enabled", True))
    display_name = str(raw.get("display_name") or "").strip()
    interval = _interval(raw.get("check_interval_minutes"), common_interval)
    if platform == "douyin":
        sec_uid = str(raw.get("sec_uid") or "").strip()
        if not sec_uid:
            return None
        return {
            "platform": "douyin",
            "source_key": tracking_source_key("douyin", sec_uid),
            "display_name": display_name,
            "profile_url": str(raw.get("profile_url") or "").strip(),
            "target_sec_uid": sec_uid,
            "sec_uid": sec_uid,
            "unique_id": "",
            "enabled": enabled,
            "check_interval_minutes": interval,
        }

    unique_id = normalize_tiktok_unique_id(
        raw.get("unique_id") or raw.get("profile_url")
    )
    sec_uid = str(raw.get("sec_uid") or "").strip()
    if not unique_id:
        return None
    profile_url = str(raw.get("profile_url") or "").strip()
    if not profile_url:
        profile_url = f"https://www.tiktok.com/@{unique_id}"
    return {
        "platform": "tiktok",
        "source_key": tracking_source_key("tiktok", sec_uid or unique_id),
        "display_name": display_name,
        "profile_url": profile_url,
        "target_sec_uid": sec_uid,
        "sec_uid": sec_uid,
        "unique_id": unique_id,
        "enabled": enabled,
        "check_interval_minutes": interval,
    }


def get_tracking_sources(
    profile_config: dict,
    include_disabled: bool = False,
) -> list[dict]:
    common_interval = _interval(profile_config.get("check_interval_minutes"))
    raw_sources = profile_config.get("tracking_sources")
    if not isinstance(raw_sources, list):
        return []

    normalized = []
    for raw in raw_sources:
        if not isinstance(raw, dict):
            continue
        source = _normalize_tracking_source(raw, common_interval)
        if source is not None:
            normalized.append(source)

    deduplicated = []
    seen = set()
    for source in normalized:
        identity = (
            source["platform"],
            source.get("sec_uid") or source.get("unique_id"),
        )
        if identity in seen:
            continue
        seen.add(identity)
        if include_disabled or source.get("enabled", True):
            deduplicated.append(source)
    return deduplicated


def serialize_tracking_sources(
    sources: Iterable[dict],
    common_interval: int = DEFAULT_SOURCE_INTERVAL_MINUTES,
) -> list[dict]:
    serialized = []
    for raw in sources:
        if not isinstance(raw, dict):
            continue
        source = _normalize_tracking_source(raw, common_interval)
        if source is None:
            continue
        row = {
            "platform": source["platform"],
            "display_name": source["display_name"],
            "profile_url": source["profile_url"],
            "enabled": source["enabled"],
            "check_interval_minutes": source["check_interval_minutes"],
        }
        if source["platform"] == "douyin":
            row["sec_uid"] = source["sec_uid"]
        else:
            row["unique_id"] = source["unique_id"]
            row["sec_uid"] = source["sec_uid"]
        serialized.append(row)
    return serialized


def tracking_source_label(source: dict) -> str:
    display_name = str(source.get("display_name") or "").strip()
    if display_name:
        return display_name
    if source.get("platform") == "tiktok":
        unique_id = str(source.get("unique_id") or "").strip()
        return f"@{unique_id}" if unique_id else "TikTok"
    sec_uid = str(source.get("sec_uid") or source.get("target_sec_uid") or "")
    return f"...{sec_uid[-10:]}" if sec_uid else "Douyin"
