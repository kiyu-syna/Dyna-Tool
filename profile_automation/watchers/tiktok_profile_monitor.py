import json
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from urllib.parse import parse_qs, quote, urlparse

from core.utils import console, logger
from application.tracking.sources import tracking_source_key
from profile_automation.watchers.profile_state import ProfileState
from services.browser.browser_profile_service import (
    connected_browser_profile,
    configure_lightweight_scan_page,
    create_background_page,
    is_browser_connection_error,
)


MAX_ITEM_IDS_PER_SCAN = 4


def _close_page_quietly(page) -> None:
    if page is None:
        return
    try:
        if not page.is_closed():
            page.close()
    except Exception:
        pass


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _default_source_key(sec_uid: str, unique_id: str) -> str:
    identity = str(sec_uid or unique_id or "unknown").strip()
    return tracking_source_key("tiktok", identity)


@dataclass
class TikTokVideo:
    """Metadata của một video TikTok; giữ shape tương thích pipeline hiện tại."""

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
    def item_id(self) -> str:
        return self.aweme_id

    @property
    def duration_seconds(self) -> float:
        return self.duration_ms / 1000.0

    @property
    def created_at(self) -> datetime:
        return datetime.fromtimestamp(self.create_time)

    def is_valid_video(self) -> bool:
        return self.duration_ms > 0


def _is_pinned_item(item: dict) -> bool:
    return isinstance(item, dict) and item.get("isPinnedItem") is True


def _is_photo_item(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    image_post = item.get("imagePost")
    if isinstance(image_post, dict):
        return True
    video = item.get("video") or {}
    return isinstance(video, dict) and _as_int(video.get("duration")) <= 0


def _urls_from_address(value) -> list[str]:
    """Normalize TikTok web/mobile address shapes into HTTP URL candidates."""
    if isinstance(value, str):
        candidate = value.strip()
        return [candidate] if candidate.startswith(("http://", "https://")) else []
    if not isinstance(value, dict):
        return []

    candidates = []
    for key in ("urlList", "url_list", "UrlList"):
        url_list = value.get(key)
        if isinstance(url_list, (list, tuple)):
            candidates.extend(url_list)
        elif isinstance(url_list, str):
            candidates.append(url_list)
    for key in ("url", "Url"):
        if isinstance(value.get(key), str):
            candidates.append(value[key])
    return [
        str(candidate).strip()
        for candidate in candidates
        if str(candidate or "").strip().startswith(("http://", "https://"))
    ]


def extract_tiktok_download_urls(video: dict) -> list[str]:
    """Extract non-watermarked play URLs first, with rendition and download fallbacks."""
    if not isinstance(video, dict):
        return []

    candidates = []
    # The web item_list currently exposes playAddr as a direct string and
    # PlayAddrStruct as three equivalent hosts. Keep both response variants.
    for key in ("playAddr", "PlayAddrStruct", "play_addr"):
        candidates.extend(_urls_from_address(video.get(key)))

    renditions = video.get("bitrateInfo") or video.get("bitRate") or video.get("bit_rate") or []
    if isinstance(renditions, list):
        # Prefer H.264 for downstream Facebook/YouTube compatibility, then the
        # highest bitrate. The ordinary playAddr remains first when available.
        def rendition_rank(item):
            if not isinstance(item, dict):
                return (1, 0)
            codec = str(item.get("CodecType") or item.get("codecType") or "").casefold()
            bitrate = _as_int(item.get("Bitrate", item.get("bitRate")))
            return (0 if "h264" in codec or "avc" in codec else 1, -bitrate)

        for rendition in sorted(renditions, key=rendition_rank):
            if not isinstance(rendition, dict):
                continue
            address = rendition.get("PlayAddr") or rendition.get("playAddr") or rendition.get("play_addr")
            candidates.extend(_urls_from_address(address))

    # Prefer TikTok's first-party /aweme/v1/play/ redirect. In practice the
    # signed CDN hosts can reject a non-browser request with 403 while this
    # first-party URL still returns 206 video/mp4 and redirects correctly.
    candidates = list(dict.fromkeys(candidates))
    candidates.sort(
        key=lambda candidate: 0
        if urlparse(candidate).netloc.casefold() == "www.tiktok.com"
        and urlparse(candidate).path.startswith("/aweme/v1/play/")
        else 1
    )

    # downloadAddr may contain TikTok's watermarked rendition, so it is the
    # final fallback rather than the primary candidate.
    for key in ("downloadAddr", "download_addr"):
        candidates.extend(_urls_from_address(video.get(key)))

    return list(dict.fromkeys(candidates))


def _parse_tiktok_item(item: dict) -> Optional[TikTokVideo]:
    """Parse itemList; video ghim và photo post luôn bị loại tại đây."""
    if not isinstance(item, dict) or _is_pinned_item(item) or _is_photo_item(item):
        return None

    try:
        item_id = str(item.get("id") or "").strip()
        video = item.get("video") or {}
        author = item.get("author") or {}
        stats = item.get("stats") or {}
        duration_seconds = _as_int(video.get("duration"))
        if not item_id or not isinstance(video, dict) or duration_seconds <= 0:
            return None

        unique_id = str(author.get("uniqueId") or "").strip()
        share_url = (
            f"https://www.tiktok.com/@{quote(unique_id, safe='._-')}/video/{item_id}"
            if unique_id
            else f"https://www.tiktok.com/video/{item_id}"
        )
        download_urls = extract_tiktok_download_urls(video)
        return TikTokVideo(
            aweme_id=item_id,
            share_url=share_url,
            desc=str(item.get("desc") or ""),
            create_time=_as_int(item.get("createTime")),
            duration_ms=duration_seconds * 1000,
            like_count=_as_int(stats.get("diggCount")),
            play_count=_as_int(stats.get("playCount")),
            author_uid=str(author.get("secUid") or author.get("id") or ""),
            author_nickname=str(author.get("nickname") or unique_id),
            download_url=download_urls[0] if download_urls else "",
            download_urls=download_urls,
        )
    except Exception as exc:
        logger.warning("Không parse được item TikTok: %s", exc)
        return None


def parse_tiktok_item_list(data: dict, limit: int = MAX_ITEM_IDS_PER_SCAN) -> List[TikTokVideo]:
    """Parse và dedupe itemList, không đưa photo/video ghim vào kết quả."""
    if not isinstance(data, dict):
        return []
    status = data.get("statusCode", data.get("status_code"))
    items = data.get("itemList")
    if status not in (None, 0) or not isinstance(items, list):
        return []

    videos: List[TikTokVideo] = []
    known_ids = set()
    for item in items:
        video = _parse_tiktok_item(item)
        if video is None or video.aweme_id in known_ids:
            continue
        known_ids.add(video.aweme_id)
        videos.append(video)
        if limit > 0 and len(videos) >= limit:
            break
    return videos


class TikTokProfileMonitor:
    """
    Theo dõi một profile TikTok bằng cách bắt response /api/post/item_list/.

    URL request có chữ ký động nên monitor để chính trang TikTok tạo request,
    không gọi lại URL API đã sao chép.
    """

    POST_ITEM_PATTERN = "/api/post/item_list/"

    def __init__(
        self,
        profile_id: str,
        unique_id: str,
        gemlogin_profile_id: str,
        sec_uid: str = "",
        api_url: str = "http://127.0.0.1:1010",
        min_likes: int = 0,
        max_duration_sec: float = 300,
        state_dir: Optional[str] = None,
        source_key: Optional[str] = None,
        profile_config: Optional[dict] = None,
    ):
        self.profile_id = str(profile_id)
        self.unique_id = str(unique_id or "").strip().lstrip("@")
        self.sec_uid = str(sec_uid or "").strip()
        self.gemlogin_profile_id = str(gemlogin_profile_id)
        self.api_url = api_url
        self.min_likes = int(min_likes or 0)
        self.max_duration_sec = float(max_duration_sec or 0)
        self.profile_config = profile_config or {}
        resolved_source_key = source_key or _default_source_key(self.sec_uid, self.unique_id)
        self.state = ProfileState(self.profile_id, resolved_source_key, state_dir)
        self.last_scan_succeeded = False

    def _matches_post_response(self, response) -> bool:
        url = str(getattr(response, "url", "") or "")
        if self.POST_ITEM_PATTERN not in url:
            return False
        if not self.sec_uid:
            return True
        try:
            response_sec_uid = parse_qs(urlparse(url).query).get("secUid", [""])[0]
            return not response_sec_uid or response_sec_uid == self.sec_uid
        except Exception:
            return True

    @staticmethod
    def _response_json(response) -> dict:
        text = response.text()
        if not text:
            return {}
        data = json.loads(text)
        return data if isinstance(data, dict) else {}

    def _scan_profile_page(
        self,
        page,
        profile_url: str,
        pages_to_fetch: int,
        timeout_per_page: float,
    ) -> tuple[List[TikTokVideo], int]:
        all_videos: List[TikTokVideo] = []
        known_ids = set()
        successful_pages = 0
        console.print(
            f"[cyan]🔍 [Profile {self.profile_id}] Đang mở profile TikTok: {profile_url}[/]"
        )

        page_count = 0
        while True:
            page_count += 1
            try:
                with page.expect_response(
                    self._matches_post_response,
                    timeout=int(timeout_per_page * 1000),
                ) as response_info:
                    if page_count == 1:
                        page.goto(profile_url, wait_until="commit", timeout=15000)
                    else:
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

                response = response_info.value
                data = self._response_json(response)
            except Exception as exc:
                if is_browser_connection_error(exc, self.profile_config):
                    raise
                logger.warning(
                    "[Profile %s] Không lấy được danh sách video ở trang %s: %s",
                    self.profile_id,
                    page_count,
                    exc,
                )
                break

            status = data.get("statusCode", data.get("status_code"))
            items = data.get("itemList") or []
            if status != 0 or not isinstance(items, list) or not items:
                logger.warning(
                    "[Profile %s] item_list không hợp lệ hoặc không có item (status=%s).",
                    self.profile_id,
                    status,
                )
                break

            successful_pages += 1

            pinned_count = sum(1 for item in items if _is_pinned_item(item))
            photo_count = sum(1 for item in items if _is_photo_item(item))
            parsed = parse_tiktok_item_list(data, limit=0)
            for video in parsed:
                if video.aweme_id in known_ids:
                    continue
                known_ids.add(video.aweme_id)
                all_videos.append(video)
                if len(all_videos) >= MAX_ITEM_IDS_PER_SCAN:
                    break

            logger.info(
                "[Profile %s] TikTok trang %s: %s mục, bỏ %s video ghim, bỏ %s bài ảnh, lấy %s video.",
                self.profile_id,
                page_count,
                len(items),
                pinned_count,
                photo_count,
                len(all_videos),
            )

            if len(all_videos) >= MAX_ITEM_IDS_PER_SCAN:
                break
            if not data.get("hasMore"):
                break
            if pages_to_fetch > 0 and page_count >= pages_to_fetch:
                break
        return all_videos, successful_pages

    def fetch_latest_videos(
        self,
        pages_to_fetch: int = 1,
        timeout_per_page: float = 20.0,
        page=None,
    ) -> List[TikTokVideo]:
        self.last_scan_succeeded = False
        if not self.unique_id:
            raise ValueError("Thiếu TikTok unique_id để mở trang profile nguồn.")

        profile_url = f"https://www.tiktok.com/@{quote(self.unique_id, safe='._-')}"

        if page is not None:
            all_videos, successful_pages = self._scan_profile_page(
                page,
                profile_url,
                pages_to_fetch,
                timeout_per_page,
            )
            self.last_scan_succeeded = successful_pages > 0
            return all_videos[:MAX_ITEM_IDS_PER_SCAN]

        with connected_browser_profile(
            self.gemlogin_profile_id,
            self.api_url,
            profile_config=self.profile_config,
            resource_saving=True,
            close_profile_on_exit=True,
        ) as browser, ExitStack() as page_cleanup:
            if not browser.contexts:
                logger.error("[Profile %s] Trình duyệt không có phiên để quét TikTok.", self.profile_id)
                return []

            context = browser.contexts[0]
            created_page = create_background_page(browser, context)
            page_cleanup.callback(_close_page_quietly, created_page)
            configure_lightweight_scan_page(created_page)

            try:
                all_videos, successful_pages = self._scan_profile_page(
                    created_page,
                    profile_url,
                    pages_to_fetch,
                    timeout_per_page,
                )
            finally:
                _close_page_quietly(created_page)

        self.last_scan_succeeded = successful_pages > 0
        return all_videos[:MAX_ITEM_IDS_PER_SCAN]

    def get_new_videos(self, page=None) -> List[TikTokVideo]:
        is_first_run = not self.state.path.exists()
        fetch_kwargs = {"pages_to_fetch": 3}
        if page is not None:
            fetch_kwargs["page"] = page

        if is_first_run:
            try:
                videos = self.fetch_latest_videos(**fetch_kwargs)
            except TypeError:
                videos = self.fetch_latest_videos(pages_to_fetch=3)
            for video in videos:
                self.state.mark_seen(video.aweme_id, video.create_time)
            console.print(
                f"[green]✅ [Profile {self.profile_id}] Đã tạo mốc ban đầu TikTok với "
                f"{len(videos)} video thường; bài ảnh và video ghim đã được bỏ qua.[/]"
            )
            return []

        fetch_kwargs["pages_to_fetch"] = 1
        try:
            videos = self.fetch_latest_videos(**fetch_kwargs)
        except TypeError:
            videos = self.fetch_latest_videos(pages_to_fetch=1)

        new_videos = []
        for video in videos:
            if not self.state.is_new_video(video.aweme_id, video.create_time):
                continue
            if video.like_count < self.min_likes:
                continue
            if video.duration_seconds > self.max_duration_sec:
                continue
            new_videos.append(video)

        new_videos.sort(key=lambda video: video.create_time)
        return new_videos

    def build_start_baseline(
        self,
        latest_count: Optional[int] = None,
        page=None,
    ) -> Optional[List[TikTokVideo]]:
        try:
            videos = (
                self.fetch_latest_videos(pages_to_fetch=1, page=page)
                if page is not None
                else self.fetch_latest_videos(pages_to_fetch=1)
            )
        except TypeError:
            videos = self.fetch_latest_videos(pages_to_fetch=1)

        if not self.last_scan_succeeded:
            return None

        newest_videos = sorted(videos, key=lambda video: video.create_time, reverse=True)
        if latest_count is not None:
            newest_videos = newest_videos[:latest_count]
        max_create_time = max((video.create_time for video in newest_videos), default=0)
        self.state.replace_seen(
            [video.aweme_id for video in newest_videos],
            max_create_time,
        )
        return newest_videos

    def mark_processed(self, video: TikTokVideo) -> None:
        self.state.mark_seen(video.aweme_id, video.create_time)

    def mark_ignored(self, video: TikTokVideo) -> None:
        self.state.mark_seen(video.aweme_id, video.create_time)
