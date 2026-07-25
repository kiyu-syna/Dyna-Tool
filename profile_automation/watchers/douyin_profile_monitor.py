import json
import time
import os
import re
import threading
from contextlib import ExitStack
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, List, Optional, Set
from pathlib import Path

from playwright.sync_api import Response
from rich import box
from rich.panel import Panel
from rich.table import Table

import core.config as config
from core.runtime_paths import profile_state_dir
from core.utils import logger, console
from profile_automation.tracking_sources import tracking_source_key
from services.browser.browser_profile_service import (
    connected_browser_profile as connected_gemlogin_profile,
    configure_lightweight_scan_page,
    create_background_page,
    is_browser_connection_error as is_gemlogin_connection_error,
)

MAX_AWEME_IDS_PER_SCAN = 4
PHOTO_AWEME_TYPES = {68}
PHOTO_MEDIA_TYPES = {2}


def _close_page_quietly(page) -> None:
    if page is None:
        return
    try:
        if not page.is_closed():
            page.close()
    except Exception:
        pass

# ─────────────────────────────────────────────
# Data model cho một video được phát hiện
# ─────────────────────────────────────────────
@dataclass
class DouyinVideo:
    aweme_id: str
    share_url: str
    desc: str
    create_time: int          # Unix timestamp
    duration_ms: int          # Milliseconds
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
        """Đảm bảo đây là video thật, không phải ảnh hay live."""
        return self.duration_ms > 0


def _build_douyin_modal_url(_sec_uid: str, aweme_id: str) -> str:
    return f"https://www.douyin.com/video/{aweme_id}"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _is_photo_aweme(item: dict) -> bool:
    """
    Nhận diện bài ảnh/carousel trước khi parser dùng thời lượng nhạc dự phòng.

    Response Douyin hiện tại có thể đặt một block ``video`` trong bài ảnh để
    phát nhạc nền. Vì vậy chỉ kiểm tra sự tồn tại của ``video`` là không đủ:
    bài ảnh thực tế thường có aweme_type=68, media_type=2 và video.duration=0.
    """
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

    rates = video.get("bit_rate") or video.get("bitRateList") or video.get("bit_rate_list") or []
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

        def menu_quality_score(rate: dict) -> tuple[int, int, int]:
            return (
                resolution_value(rate),
                numeric_value(rate.get("data_size") or rate.get("dataSize") or rate.get("size")),
                numeric_value(rate.get("bit_rate") or rate.get("bitRate")),
            )

        rates = sorted(
            (rate for rate in rates if isinstance(rate, dict)),
            key=menu_quality_score,
            reverse=True,
        )
        for rate in rates:
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


def _parse_aweme(item: dict, sec_uid: Optional[str] = None) -> Optional[DouyinVideo]:
    """Parse một item từ aweme_list thành DouyinVideo object."""
    try:
        if not isinstance(item, dict):
            return None

        # Bo qua video ghim
        if item.get("is_top", 0) == 1:
            return None

        # Bỏ qua ảnh/carousel trước khi duration nhạc bị dùng làm duration video.
        if _is_photo_aweme(item):
            return None

        # Bỏ qua video đã xóa hoặc riêng tư
        status = item.get("status", {})
        if status.get("is_delete", 0) or status.get("private_status", 0):
            return None

        stats = item.get("statistics", {})
        video = item.get("video", {})
        author = item.get("author", {})

        # Lấy thời lượng (duration) linh hoạt từ nhiều cấp độ trong JSON Douyin
        duration_ms = item.get("duration", 0)  # Cấp 1: Root item
        if not duration_ms and video:
            duration_ms = video.get("duration", 0)  # Cấp 2: Video block
        if not duration_ms and item.get("music"):
            # Cấp 3: Music duration (thường tính bằng giây, cần nhân với 1000)
            duration_ms = item.get("music", {}).get("duration", 0) * 1000

        aweme_id = str(item["aweme_id"])
        share_url = _build_douyin_modal_url(sec_uid, aweme_id) if sec_uid else item.get(
            "share_url",
            f"https://www.douyin.com/video/{aweme_id}",
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


def _short_text(value: Any, max_len: int = 54) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _format_time(timestamp: int) -> str:
    if not timestamp:
        return "?"
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(timestamp)


def _analyze_aweme_payload(data: dict) -> dict:
    aweme_list = data.get("aweme_list", []) or []
    analysis = {
        "json_keys": len(data.keys()),
        "aweme_count": len(aweme_list),
        "valid_video_count": 0,
        "top_video_count": 0,
        "image_post_count": 0,
        "private_or_deleted_count": 0,
        "invalid_duration_count": 0,
        "sample_ids": [],
    }

    for item in aweme_list:
        aweme_id = item.get("aweme_id")
        if aweme_id and len(analysis["sample_ids"]) < MAX_AWEME_IDS_PER_SCAN:
            analysis["sample_ids"].append(str(aweme_id))

        if item.get("is_top", 0) == 1:
            analysis["top_video_count"] += 1
            continue

        if _is_photo_aweme(item):
            analysis["image_post_count"] += 1
            continue

        status = item.get("status", {}) or {}
        if status.get("is_delete", 0) or status.get("private_status", 0):
            analysis["private_or_deleted_count"] += 1
            continue

        video = _parse_aweme(item)
        if not video or not video.is_valid_video():
            analysis["invalid_duration_count"] += 1
            continue

        analysis["valid_video_count"] += 1

    return analysis


def _print_response_analysis(
    profile_id: str,
    page_count: int,
    response_url: str,
    data: dict,
    parsed_videos: List[DouyinVideo],
    total_videos: int,
):
    analysis = _analyze_aweme_payload(data)

    table = Table(
        title=f"Profile {profile_id} · Phân tích JSON Douyin · Trang {page_count}",
        box=box.SIMPLE_HEAVY,
        show_lines=False,
    )
    table.add_column("Hạng mục", style="cyan", no_wrap=True)
    table.add_column("Giá trị", style="white")
    table.add_column("Ghi chú", style="dim")

    table.add_row("API status", str(data.get("status_code")), "0 nghĩa là response hợp lệ")
    table.add_row("aweme_list", str(analysis["aweme_count"]), "Số item thô trong JSON")
    table.add_row("Video hợp lệ", str(analysis["valid_video_count"]), "Đã parse được và có duration")
    table.add_row("Video ghim", str(analysis["top_video_count"]), "Bo qua vi is_top=1")
    table.add_row("Ảnh/carousel", str(analysis["image_post_count"]), "Bỏ qua vì không phải video")
    table.add_row("Riêng tư/đã xóa", str(analysis["private_or_deleted_count"]), "Bỏ qua theo status")
    table.add_row("Duration lỗi", str(analysis["invalid_duration_count"]), "Bỏ qua vì không có thời lượng")
    table.add_row("has_more", str(data.get("has_more", "?")), "1 còn trang, 0 hết trang")
    table.add_row("cursor", str(data.get("max_cursor", "?")), "Cursor trang tiếp theo")
    table.add_row("Tổng đã gom", str(total_videos), "Tổng video hợp lệ trong vòng quét")
    table.add_row(
        "ID mẫu",
        ", ".join(analysis["sample_ids"]) or "-",
        f"{MAX_AWEME_IDS_PER_SCAN} aweme_id đầu tiên",
    )

    console.print(Panel(table, title="📦 Gói tin bắt được", border_style="green"))
    console.print(f"[dim]URL: {_short_text(response_url, 140)}[/]")

    if parsed_videos:
        _print_video_table(profile_id, parsed_videos, title=f"Video parse được từ trang {page_count}")


def _print_video_table(profile_id: str, videos: List[DouyinVideo], title: Optional[str] = None):
    table = Table(
        title=title or f"Profile {profile_id} · Video đã lấy được",
        box=box.SIMPLE,
        show_lines=False,
    )
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("Aweme ID", style="cyan", no_wrap=True)
    table.add_column("Thời gian", style="green", no_wrap=True)
    table.add_column("Like", justify="right", style="magenta", no_wrap=True)
    table.add_column("View", justify="right", style="yellow", no_wrap=True)
    table.add_column("Giây", justify="right", no_wrap=True)
    table.add_column("Mô tả", style="white")

    shown_videos = videos[:12]
    for index, video in enumerate(shown_videos, start=1):
        table.add_row(
            str(index),
            video.aweme_id,
            _format_time(video.create_time),
            f"{video.like_count:,}",
            f"{video.play_count:,}",
            f"{video.duration_seconds:.1f}",
            _short_text(video.desc),
        )

    if len(videos) > len(shown_videos):
        table.caption = f"Đang hiển thị {len(shown_videos)}/{len(videos)} video để terminal không bị quá dài."

    console.print(table)


# ─────────────────────────────────────────────
# State persistence: lưu/load seen_ids
# ─────────────────────────────────────────────
class ProfileState:
    """
    Lưu trạng thái đã xử lý cho từng profile vào file JSON.
    Thread-safe.
    """
    def __init__(
        self,
        profile_id: str,
        source_key: str,
        state_dir: Optional[str] = None,
    ):
        self.profile_id = profile_id
        if state_dir is None:
            state_dir = str(profile_state_dir())
        self.path = Path(state_dir) / f"profile_{profile_id}_source_{source_key}_seen.json"
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"seen_ids": [], "last_check": None, "last_video_create_time": 0}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    @property
    def seen_ids(self) -> Set[str]:
        with self._lock:
            return set(self._data.get("seen_ids", []))

    @property
    def last_video_create_time(self) -> int:
        with self._lock:
            return self._data.get("last_video_create_time", 0)

    def mark_seen(self, video_id: str, create_time: int = 0):
        with self._lock:
            if "seen_ids" not in self._data:
                self._data["seen_ids"] = []
            if video_id not in self._data["seen_ids"]:
                self._data["seen_ids"].append(video_id)
                # Giữ tối đa 500 ID để file không phình to
                if len(self._data["seen_ids"]) > 500:
                    self._data["seen_ids"] = self._data["seen_ids"][-500:]
            if create_time > self._data.get("last_video_create_time", 0):
                self._data["last_video_create_time"] = create_time
            self._data["last_check"] = datetime.now().isoformat()
            self._save()

    def replace_seen(self, video_ids: List[str], last_video_create_time: int = 0):
        with self._lock:
            deduped_ids = []
            seen = set()
            for video_id in video_ids:
                if not video_id or video_id in seen:
                    continue
                seen.add(video_id)
                deduped_ids.append(video_id)
            self._data["seen_ids"] = deduped_ids[-500:]
            self._data["last_video_create_time"] = int(last_video_create_time or 0)
            self._data["last_check"] = datetime.now().isoformat()
            self._save()

    def is_new_video(self, video_id: str, create_time: int) -> bool:
        """
        Video chỉ được coi là đã xử lý khi ID của nó nằm trong seen_ids.

        Không loại theo last_video_create_time vì nhiều video có thể nằm trong cùng
        hàng đợi; một video mới hơn thành công không được làm mất lượt retry của
        video cũ hơn đã thất bại.
        """
        with self._lock:
            seen = set(self._data.get("seen_ids", []))
            return video_id not in seen


# ─────────────────────────────────────────────
# Monitor chính
# ─────────────────────────────────────────────
class DouyinProfileMonitor:
    """
    Monitor một kênh Douyin cụ thể.
    Dùng Playwright intercept để bắt response từ /aweme/v1/web/aweme/post/
    mà không cần gọi API trực tiếp.
    """

    AWEME_POST_PATTERN = "/aweme/v1/web/aweme/post/"

    def __init__(
        self,
        profile_id: str,
        sec_uid: str,                    # sec_uid của kênh Douyin nguồn
        gemlogin_profile_id: str,        # GemLogin profile ID cho tài khoản Douyin
        api_url: str = "http://127.0.0.1:1010",
        min_likes: int = 0,
        max_duration_sec: float = 300,
        state_dir: Optional[str] = None,
        source_key: Optional[str] = None,
        profile_config: Optional[dict] = None,
    ):
        self.profile_id = profile_id
        self.sec_uid = sec_uid
        self.gemlogin_profile_id = gemlogin_profile_id
        self.api_url = api_url
        self.min_likes = min_likes
        self.max_duration_sec = max_duration_sec
        self.profile_config = profile_config or {}
        resolved_source_key = source_key or tracking_source_key("douyin", sec_uid)
        self.state = ProfileState(profile_id, resolved_source_key, state_dir)

        self._captured_responses: List[dict] = []
        self._capture_lock = threading.Lock()

    def _on_response(self, response: Response):
        """Playwright response handler — chạy trong playwright thread."""
        if self.AWEME_POST_PATTERN not in response.url:
            return
        try:
            logger.info(f"[BẮT GÓI] Phát hiện gói tin mạng khớp mẫu: {response.url[:120]}...")
            
            # Sử dụng response.text() để lấy chuỗi thô rất nhanh qua mạng, tránh nghẽn CDP
            text = response.text()
            if not text:
                return
                
            import json
            data = json.loads(text)
            status = data.get("status_code")
            aweme_list = data.get("aweme_list", [])
            logger.info(f"[BẮT GÓI] JSON mã trạng thái: {status} | Số lượng aweme: {len(aweme_list)}")
            
            if status == 0 and "aweme_list" in data:
                with self._capture_lock:
                    self._captured_responses.append(data)
                    logger.info(f"[BẮT GÓI] Đã thêm thành công {len(aweme_list)} video vào bộ nhớ đệm để xử lý.")
        except Exception as e:
            logger.error(f"[LỖI BẮT GÓI] Lỗi phân tích gói tin JSON: {e}")

    def fetch_latest_videos(
        self,
        pages_to_fetch: int = 1,    # Mặc định chỉ lấy trang đầu
        timeout_per_page: float = 20.0,
    ) -> List[DouyinVideo]:
        """
        Mở trang profile Douyin, bắt response API, parse danh sách video.
        """
        all_videos: List[DouyinVideo] = []
        profile_url = f"https://www.douyin.com/user/{self.sec_uid}"

        with connected_gemlogin_profile(
            self.gemlogin_profile_id,
            self.api_url,
            profile_config=self.profile_config,
            resource_saving=True,
            close_profile_on_exit=True,
        ) as browser, ExitStack() as page_cleanup:
            if not browser.contexts:
                logger.error(f"[Profile {self.profile_id}] CDP đã kết nối nhưng không có browser context nào.")
                return []
            context = browser.contexts[0]
            page = create_background_page(browser, context)
            page_cleanup.callback(_close_page_quietly, page)
            configure_lightweight_scan_page(page)

            console.print(f"[cyan]🔍 [Profile {self.profile_id}] Đang mở trang profile: {profile_url}[/]")

            try:
                page_count = 0
                while True:
                    page_count += 1

                    # Dùng expect_response để chặn bắt đúng 1 gói tin mạng từ Douyin
                    try:
                        with page.expect_response(
                            lambda r: self.AWEME_POST_PATTERN in r.url,
                            timeout=int(timeout_per_page * 1000)
                        ) as response_info:
                            if page_count == 1:
                                try:
                                    page.goto(profile_url, wait_until="commit", timeout=15000)
                                except Exception as goto_err:
                                    logger.debug(f"[Profile {self.profile_id}] goto: {goto_err}")
                            else:
                                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

                        response = response_info.value
                        import json as _json
                        raw_text = response.text()
                        data = _json.loads(raw_text)

                    except Exception as wait_err:
                        if is_gemlogin_connection_error(wait_err, self.profile_config):
                            raise
                        logger.warning(f"[Profile {self.profile_id}] Không bắt được gói tin trang {page_count}: {wait_err}")
                        break

                    status = data.get("status_code")
                    aweme_list = data.get("aweme_list", [])
                    logger.info(f"[BẮT GÓI ✅] Trang {page_count}: trạng thái={status} | {len(aweme_list)} video")

                    if status != 0 or not aweme_list:
                        logger.warning(f"[Profile {self.profile_id}] Gói tin hợp lệ nhưng không có video. Dừng.")
                        break

                    parsed_page_videos: List[DouyinVideo] = []
                    known_aweme_ids = {
                        video.aweme_id for video in all_videos
                    }
                    for item in aweme_list:
                        video = _parse_aweme(item, self.sec_uid)
                        if (
                            video
                            and video.is_valid_video()
                            and video.aweme_id not in known_aweme_ids
                        ):
                            parsed_page_videos.append(video)
                            known_aweme_ids.add(video.aweme_id)
                        if len(all_videos) + len(parsed_page_videos) >= MAX_AWEME_IDS_PER_SCAN:
                            break

                    all_videos.extend(parsed_page_videos)

                    # In bảng phân tích terminal sau khi bắt được JSON
                    _print_response_analysis(
                        self.profile_id,
                        page_count,
                        response.url,
                        data,
                        parsed_page_videos,
                        len(all_videos),
                    )

                    if len(all_videos) >= MAX_AWEME_IDS_PER_SCAN:
                        console.print(
                            f"[dim]   → Đã lấy đủ tối đa {MAX_AWEME_IDS_PER_SCAN} aweme_id[/]"
                        )
                        break

                    has_more = data.get("has_more", 0)
                    if not has_more:
                        console.print(f"[dim]   → Không còn trang nào (has_more=0)[/]")
                        break

                    if pages_to_fetch > 0 and page_count >= pages_to_fetch:
                        console.print(f"[dim]   → Đã lấy đủ {pages_to_fetch} trang[/]")
                        break

            except Exception as e:
                import traceback
                logger.error(f"[Profile {self.profile_id}] Lỗi nghiêm trọng trong fetch_latest_videos: {e}")
                logger.error(traceback.format_exc())
            finally:
                try:
                    _close_page_quietly(page)
                    logger.info(f"[Profile {self.profile_id}] Đã đóng tab quét nguồn Douyin.")
                except Exception:
                    pass

        console.print(f"[green]   → Tổng cộng lấy được {len(all_videos)} video từ kênh nguồn[/]")
        return all_videos[:MAX_AWEME_IDS_PER_SCAN]

    def get_new_videos(self) -> List[DouyinVideo]:
        """
        Lấy danh sách video MỚI chưa được xử lý của kênh này.
        Lọc theo: seen_ids, min_likes, max_duration.
        Kết quả được sắp xếp: video cũ nhất trước (để upload theo thứ tự đăng gốc).
        """
        is_first_run = not self.state.path.exists()

        if is_first_run:
            console.print(f"[yellow]⚠️  [Profile {self.profile_id}] Lần đầu chạy — đang build baseline seen_ids...[/]")
            # Lần đầu: lấy 3 trang để có đủ context, đánh dấu tất cả là "đã thấy"
            all_videos = self.fetch_latest_videos(pages_to_fetch=3)
            for v in all_videos:
                self.state.mark_seen(v.aweme_id, v.create_time)
            console.print(f"[green]✅ [Profile {self.profile_id}] Đã build baseline với {len(all_videos)} video. Vòng tiếp theo sẽ phát hiện video mới.[/]")
            return []  # Không xử lý gì ở lần đầu

        # Các lần sau: chỉ cần lấy trang đầu
        videos = self.fetch_latest_videos(pages_to_fetch=1)

        # Lọc video mới
        new_videos = []
        for v in videos:
            if not self.state.is_new_video(v.aweme_id, v.create_time):
                continue
            if v.like_count < self.min_likes:
                logger.debug(f"   Skip {v.aweme_id}: {v.like_count} likes < {self.min_likes}")
                continue
            if v.duration_seconds > self.max_duration_sec:
                logger.debug(f"   Skip {v.aweme_id}: {v.duration_seconds:.0f}s > {self.max_duration_sec}s")
                continue
            new_videos.append(v)

        # Sắp xếp: video cũ nhất trước (upload theo thứ tự thời gian)
        new_videos.sort(key=lambda v: v.create_time)

        if new_videos:
            console.print(f"[bold green]🆕 [Profile {self.profile_id}] Phát hiện {len(new_videos)} video mới![/]")
        
        return new_videos

    def build_start_baseline(self, latest_count: Optional[int] = None) -> Optional[List[DouyinVideo]]:
        """Lưu các video hiện có khi bấm Start, không tải hoặc đăng video nào."""
        videos = self.fetch_latest_videos(pages_to_fetch=1)
        if not videos:
            return None

        newest_videos = sorted(videos, key=lambda v: v.create_time, reverse=True)
        if latest_count is not None:
            newest_videos = newest_videos[:latest_count]
        max_create_time = max((v.create_time for v in newest_videos), default=0)
        self.state.replace_seen([v.aweme_id for v in newest_videos], max_create_time)
        console.print(
            f"[green]✓ [Profile {self.profile_id}] Start baseline đã ghi nhớ "
            f"{len(newest_videos)} video hiện có.[/]"
        )
        return newest_videos

    def mark_processed(self, video: DouyinVideo):
        """Đánh dấu video đã được xử lý xong (sau khi upload thành công)."""
        self.state.mark_seen(video.aweme_id, video.create_time)

    def mark_ignored(self, video: DouyinVideo):
        """Đánh dấu video cũ hơn đã bỏ qua khi chỉ giữ video mới nhất của lượt quét."""
        self.state.mark_seen(video.aweme_id, video.create_time)
