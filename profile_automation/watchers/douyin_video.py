from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


PHOTO_AWEME_TYPES = {68}
PHOTO_MEDIA_TYPES = {2}


@dataclass
class DouyinVideo:
    aweme_id: str
    share_url: str
    desc: str
    create_time: int
    duration_ms: int
    like_count: int
    play_count: int
    author_uid: str
    author_nickname: str
    download_url: str = ""
    download_urls: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return self.duration_ms / 1000.0

    @property
    def created_at(self) -> datetime:
        return datetime.fromtimestamp(self.create_time)

    def is_valid_video(self) -> bool:
        return self.duration_ms > 0


def _build_douyin_modal_url(_sec_uid: str, aweme_id: str) -> str:
    return f"https://www.douyin.com/video/{aweme_id}"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _is_photo_aweme(item: dict) -> bool:
    """Detect a photo/carousel before music duration can be used as fallback."""
    if not isinstance(item, dict):
        return False
    if item.get("image_post_info") is not None:
        return True
    if _as_int(item.get("aweme_type"), default=-1) in PHOTO_AWEME_TYPES:
        return True

    video = item.get("video") or item.get("videoInfo") or item.get("video_info") or {}
    explicit_duration = max(
        _as_int(item.get("duration")),
        _as_int(video.get("duration")) if isinstance(video, dict) else 0,
    )
    return (
        _as_int(item.get("media_type"), default=-1) in PHOTO_MEDIA_TYPES
        and explicit_duration <= 0
    )


def _http_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            urls.append(value)
        return urls
    if isinstance(value, list):
        for item in value:
            urls.extend(_http_urls(item))
        return list(dict.fromkeys(urls))
    if isinstance(value, dict):
        for key in ("url_list", "urlList", "url", "uri"):
            urls.extend(_http_urls(value.get(key)))
    return list(dict.fromkeys(urls))


def extract_douyin_download_urls(aweme: dict) -> list[str]:
    """Extract direct video URLs in descending quality order."""
    if not isinstance(aweme, dict):
        return []
    video = aweme.get("video") or aweme.get("videoInfo") or aweme.get("video_info") or {}
    if not isinstance(video, dict):
        return []

    candidates: list[str] = []

    def add_urls(value: Any) -> None:
        for url in _http_urls(value):
            if url not in candidates:
                candidates.append(url)

    rates = (
        video.get("bit_rate")
        or video.get("bitRateList")
        or video.get("bit_rate_list")
        or []
    )
    if isinstance(rates, list):

        def numeric_value(value) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        def resolution_value(rate: dict) -> int:
            width = numeric_value(rate.get("width"))
            height = numeric_value(rate.get("height"))
            if width > 0 and height > 0:
                return min(width, height)
            label = str(
                rate.get("resolution")
                or rate.get("gear_name")
                or rate.get("gearName")
                or rate.get("quality_type")
                or ""
            )
            match = re.search(r"(\d+)[pP]", label)
            if match:
                return int(match.group(1))
            if re.search(r"\b8k\b", label, re.IGNORECASE):
                return 4320
            if re.search(r"\b4k\b", label, re.IGNORECASE):
                return 2160
            return 0

        def quality_score(rate: dict) -> tuple[int, int, int]:
            return (
                resolution_value(rate),
                numeric_value(
                    rate.get("data_size")
                    or rate.get("dataSize")
                    or rate.get("size")
                ),
                numeric_value(rate.get("bit_rate") or rate.get("bitRate")),
            )

        for rate in sorted(
            (rate for rate in rates if isinstance(rate, dict)),
            key=quality_score,
            reverse=True,
        ):
            for key in ("play_addr", "playAddr", "play_api", "playApi"):
                add_urls(rate.get(key))

    for key in (
        "play_addr_h264",
        "playAddrH264",
        "play_addr",
        "playAddr",
        "play_api",
        "playApi",
        "download_addr",
        "downloadAddr",
    ):
        add_urls(video.get(key))
    return candidates


def _parse_aweme(item: dict, sec_uid: str | None = None) -> DouyinVideo | None:
    try:
        if not isinstance(item, dict):
            return None
        if item.get("is_top", 0) == 1 or _is_photo_aweme(item):
            return None

        status = item.get("status", {})
        if status.get("is_delete", 0) or status.get("private_status", 0):
            return None

        stats = item.get("statistics", {})
        video = item.get("video", {})
        author = item.get("author", {})
        duration_ms = item.get("duration", 0)
        if not duration_ms and video:
            duration_ms = video.get("duration", 0)
        if not duration_ms and item.get("music"):
            duration_ms = item.get("music", {}).get("duration", 0) * 1000

        aweme_id = str(item["aweme_id"])
        share_url = (
            _build_douyin_modal_url(sec_uid, aweme_id)
            if sec_uid
            else item.get("share_url", f"https://www.douyin.com/video/{aweme_id}")
        )
        download_urls = extract_douyin_download_urls(item)
        return DouyinVideo(
            aweme_id=aweme_id,
            share_url=share_url,
            desc=item.get("desc", ""),
            create_time=item.get("create_time", 0),
            duration_ms=int(duration_ms),
            like_count=stats.get("digg_count", 0),
            play_count=stats.get("play_count", 0),
            author_uid=author.get("uid", ""),
            author_nickname=author.get("nickname", ""),
            download_url=download_urls[0] if download_urls else "",
            download_urls=download_urls,
        )
    except (KeyError, TypeError, ValueError):
        return None
