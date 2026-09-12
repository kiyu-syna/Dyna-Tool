from __future__ import annotations

import json
import re
import threading
import time
from contextlib import ExitStack

from playwright.sync_api import Response

from application.tracking.sources import tracking_source_key
from core.utils import console, logger
from profile_automation.watchers.douyin_console import (
    MAX_AWEME_IDS_PER_SCAN,
    print_response_analysis,
)
from profile_automation.watchers.douyin_video import DouyinVideo, _parse_aweme
from profile_automation.watchers.profile_state import ProfileState
from services.browser.browser_profile_service import (
    connected_browser_profile as connected_gemlogin_profile,
    create_background_page,
    is_browser_connection_error as is_gemlogin_connection_error,
)


def _close_page_quietly(page) -> None:
    if page is None:
        return
    try:
        if not page.is_closed():
            page.close()
    except Exception:
        pass


def _decode_douyin_response(response: Response) -> dict:
    text = response.text()
    status = int(getattr(response, "status", 0) or 0)
    headers = dict(getattr(response, "headers", {}) or {})
    content_length = str(headers.get("content-length") or len(text))
    if not text.strip():
        raise ValueError(
            f"Douyin trả dữ liệu rỗng (HTTP {status}, độ dài={content_length})."
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Douyin trả dữ liệu không đúng định dạng JSON (HTTP {status})."
        ) from exc
    if not isinstance(data, dict):
        raise ValueError("Dữ liệu JSON của Douyin không đúng cấu trúc.")
    return data


_SENSITIVE_DOUYIN_QUERY_KEYS = (
    "msToken",
    "a_bogus",
    "x-secsdk-web-signature",
    "verifyFp",
    "fp",
    "uifid",
)


def _loggable_douyin_url(url: str) -> str:
    result = str(url or "")
    for key in _SENSITIVE_DOUYIN_QUERY_KEYS:
        result = re.sub(
            rf"([?&]{re.escape(key)}=)[^&]*",
            rf"\1<redacted>",
            result,
            flags=re.IGNORECASE,
        )
    return result


class DouyinProfileMonitor:
    """Collect videos from one Douyin source through intercepted responses."""

    AWEME_POST_PATTERN = "/aweme/v1/web/aweme/post/"
    REJECTED_RESPONSE_GRACE_SECONDS = 5.0
    FIRST_PAGE_NAVIGATION_ATTEMPTS = 2

    def __init__(
        self,
        profile_id: str,
        sec_uid: str,
        gemlogin_profile_id: str,
        api_url: str = "http://127.0.0.1:1010",
        min_likes: int = 0,
        max_duration_sec: float = 300,
        state_dir: str | None = None,
        source_key: str | None = None,
        profile_config: dict | None = None,
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
        self._captured_responses: list[Response] = []
        self._capture_lock = threading.Lock()
        self._last_capture_http_statuses: list[int] = []
        self.last_scan_succeeded = False

    def _on_response(self, response: Response) -> None:
        url = str(getattr(response, "url", "") or "")
        if self.AWEME_POST_PATTERN not in url:
            return
        with self._capture_lock:
            self._captured_responses.append(response)

    @staticmethod
    def _remove_response_listener(page, listener) -> None:
        try:
            page.remove_listener("response", listener)
        except Exception:
            try:
                page.off("response", listener)
            except Exception:
                pass

    def _capture_page(
        self,
        page,
        profile_url: str,
        page_count: int,
        timeout_per_page: float,
        reload_page: bool = False,
    ) -> tuple[Response, dict] | None:
        with self._capture_lock:
            self._captured_responses.clear()
        self._last_capture_http_statuses = []
        response_listener = self._on_response
        page.on("response", response_listener)
        deadline = time.monotonic() + timeout_per_page
        next_response_index = 0
        seen_statuses: list[int] = []
        rejected_response_deadline: float | None = None
        try:
            if reload_page:
                try:
                    page.goto(profile_url, wait_until="commit", timeout=15000)
                except Exception as exc:
                    logger.debug(
                        "[Profile %s] navigate lại profile: %s",
                        self.profile_id,
                        exc,
                    )
            elif page_count == 1:
                try:
                    page.goto(profile_url, wait_until="commit", timeout=15000)
                except Exception as exc:
                    logger.debug("[Profile %s] goto: %s", self.profile_id, exc)
            else:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

            while True:
                with self._capture_lock:
                    pending = self._captured_responses[next_response_index:]
                    next_response_index = len(self._captured_responses)

                for response in pending:
                    status = int(getattr(response, "status", 0) or 0)
                    seen_statuses.append(status)
                    self._last_capture_http_statuses.append(status)
                    try:
                        data = _decode_douyin_response(response)
                    except Exception as exc:
                        if is_gemlogin_connection_error(exc, self.profile_config):
                            raise
                        logger.warning(
                            "[Profile %s] Douyin trả dữ liệu không đọc được (HTTP %s): %s",
                            self.profile_id,
                            status,
                            exc,
                        )
                        if rejected_response_deadline is None:
                            rejected_response_deadline = (
                                time.monotonic()
                                + self.REJECTED_RESPONSE_GRACE_SECONDS
                            )
                        continue

                    aweme_list = data.get("aweme_list", [])
                    if (
                        status == 200
                        and data.get("status_code") == 0
                        and "aweme_list" in data
                        and isinstance(aweme_list, list)
                    ):
                        if len(seen_statuses) > 1:
                            logger.info(
                                "[Profile %s] Đã nhận dữ liệu Douyin hợp lệ sau %s lần phản hồi.",
                                self.profile_id,
                                len(seen_statuses),
                            )
                        return response, data

                    try:
                        body_preview = " ".join(response.text()[:500].split())
                    except Exception:
                        body_preview = "<không đọc được body>"
                    logger.warning(
                        "[Profile %s] Bỏ qua dữ liệu Douyin không hợp lệ "
                        "(HTTP %s, mã trạng thái=%s).",
                        self.profile_id,
                        status,
                        data.get("status_code"),
                    )
                    logger.debug(
                        "[Profile %s] Nội dung phản hồi Douyin bị bỏ qua: %r",
                        self.profile_id,
                        body_preview,
                    )
                    if rejected_response_deadline is None:
                        rejected_response_deadline = (
                            time.monotonic()
                            + self.REJECTED_RESPONSE_GRACE_SECONDS
                        )

                effective_deadline = min(
                    deadline,
                    rejected_response_deadline or deadline,
                )
                remaining = effective_deadline - time.monotonic()
                if remaining <= 0:
                    break
                page.wait_for_timeout(min(250, max(1, int(remaining * 1000))))

            if not seen_statuses:
                logger.warning(
                    "[Profile %s] Không nhận được dữ liệu video Douyin ở trang %s "
                    "trong %.1f giây.",
                    self.profile_id,
                    page_count,
                    timeout_per_page,
                )
            else:
                logger.warning(
                    "[Profile %s] Douyin đã phản hồi %s lần ở trang %s "
                    "nhưng không có dữ liệu video hợp lệ (HTTP=%s).",
                    self.profile_id,
                    len(seen_statuses),
                    page_count,
                    seen_statuses,
                )
            return None
        except Exception as exc:
            if is_gemlogin_connection_error(exc, self.profile_config):
                raise
            logger.warning(
                "[Profile %s] Lỗi trong lúc chờ dữ liệu Douyin ở trang %s: %s",
                self.profile_id,
                page_count,
                exc,
            )
            return None
        finally:
            self._remove_response_listener(page, response_listener)

    def _parse_page(
        self,
        aweme_list: list[dict],
        known_aweme_ids: set[str],
        remaining: int,
    ) -> list[DouyinVideo]:
        parsed_videos: list[DouyinVideo] = []
        for item in aweme_list:
            video = _parse_aweme(item, self.sec_uid)
            if (
                video
                and video.is_valid_video()
                and video.aweme_id not in known_aweme_ids
            ):
                parsed_videos.append(video)
                known_aweme_ids.add(video.aweme_id)
            if len(parsed_videos) >= remaining:
                break
        return parsed_videos

    def fetch_latest_videos(
        self,
        pages_to_fetch: int = 1,
        timeout_per_page: float = 20.0,
    ) -> list[DouyinVideo]:
        self.last_scan_succeeded = False
        all_videos: list[DouyinVideo] = []
        raw_aweme_count = 0
        successful_pages = 0
        profile_url = f"https://www.douyin.com/user/{self.sec_uid}"
        with connected_gemlogin_profile(
            self.gemlogin_profile_id,
            self.api_url,
            profile_config=self.profile_config,
            # Preserve Chrome's normal network/runtime behavior. Douyin signs
            # this request in-page, so browser-wide resource saving and request
            # interception can invalidate a request that DevTools accepts.
            resource_saving=False,
            close_profile_on_exit=True,
        ) as browser, ExitStack() as page_cleanup:
            if not browser.contexts:
                logger.error(
                    "[Profile %s] CDP đã kết nối nhưng không có context.",
                    self.profile_id,
                )
                return []
            context = browser.contexts[0]
            page = create_background_page(browser, context)
            page_cleanup.callback(_close_page_quietly, page)
            console.print(
                f"[cyan]🔍 [Profile {self.profile_id}] Mở profile: {profile_url}[/]"
            )

            try:
                page_count = 0
                while True:
                    page_count += 1
                    navigation_attempts = (
                        self.FIRST_PAGE_NAVIGATION_ATTEMPTS
                        if page_count == 1
                        else 1
                    )
                    captured = None
                    for navigation_attempt in range(1, navigation_attempts + 1):
                        captured = self._capture_page(
                            page,
                            profile_url,
                            page_count,
                            timeout_per_page,
                            reload_page=navigation_attempt > 1,
                        )
                        if captured is not None:
                            break
                        if 403 not in self._last_capture_http_statuses:
                            break
                        if navigation_attempt < navigation_attempts:
                            logger.warning(
                                "[Profile %s] Douyin từ chối truy cập (HTTP 403), "
                                "đang tải lại trang (lần %s/%s).",
                                self.profile_id,
                                navigation_attempt,
                                navigation_attempts,
                            )
                    if captured is None:
                        break
                    response, data = captured
                    aweme_list = data.get("aweme_list", [])
                    successful_pages += 1
                    raw_aweme_count += len(aweme_list)
                    if data.get("status_code") != 0 or not aweme_list:
                        logger.warning(
                            "[Profile %s] Douyin không trả về video nào.",
                            self.profile_id,
                        )
                        break

                    known_ids = {video.aweme_id for video in all_videos}
                    parsed = self._parse_page(
                        aweme_list,
                        known_ids,
                        MAX_AWEME_IDS_PER_SCAN - len(all_videos),
                    )
                    all_videos.extend(parsed)
                    print_response_analysis(
                        self.profile_id,
                        page_count,
                        response.url,
                        data,
                        parsed,
                        len(all_videos),
                    )
                    if len(all_videos) >= MAX_AWEME_IDS_PER_SCAN:
                        break
                    if not data.get("has_more", 0):
                        break
                    if pages_to_fetch > 0 and page_count >= pages_to_fetch:
                        break
            except Exception as exc:
                logger.exception(
                    "[Profile %s] Lỗi khi lấy danh sách video Douyin: %s",
                    self.profile_id,
                    exc,
                )
            finally:
                _close_page_quietly(page)
                self.last_scan_succeeded = successful_pages > 0
                if successful_pages:
                    logger.info(
                        "[Profile %s] QUÉT DOUYIN THÀNH CÔNG: nhận %s mục | "
                        "lấy %s video hợp lệ | không đưa vào danh sách %s | trang %s.",
                        self.profile_id,
                        raw_aweme_count,
                        len(all_videos),
                        max(0, raw_aweme_count - len(all_videos)),
                        successful_pages,
                    )
                else:
                    logger.warning(
                        "[Profile %s] QUÉT DOUYIN KHÔNG THÀNH CÔNG: "
                        "không lấy được trang dữ liệu hợp lệ.",
                        self.profile_id,
                    )
                logger.info("[Profile %s] Đã đóng tab quét Douyin.", self.profile_id)

        console.print(
            f"[green]   → Lấy được {len(all_videos)} video từ kênh nguồn[/]"
        )
        return all_videos[:MAX_AWEME_IDS_PER_SCAN]

    def get_new_videos(self) -> list[DouyinVideo]:
        is_first_run = not self.state.path.exists()
        if is_first_run:
            console.print(
                f"[yellow]⚠️ [Profile {self.profile_id}] Đang tạo mốc ban đầu...[/]"
            )
            all_videos = self.fetch_latest_videos(pages_to_fetch=3)
            for video in all_videos:
                self.state.mark_seen(video.aweme_id, video.create_time)
            logger.info(
                "[Profile %s] KẾT QUẢ QUÉT DOUYIN: tạo mốc ban đầu với %s video | "
                "video mới đưa vào xử lý 0.",
                self.profile_id,
                len(all_videos),
            )
            return []

        videos = self.fetch_latest_videos(pages_to_fetch=1)
        already_seen_count = 0
        below_likes_count = 0
        over_duration_count = 0
        new_videos: list[DouyinVideo] = []
        for video in videos:
            if not self.state.is_new_video(video.aweme_id, video.create_time):
                already_seen_count += 1
                continue
            if video.like_count < self.min_likes:
                below_likes_count += 1
                continue
            if video.duration_seconds > self.max_duration_sec:
                over_duration_count += 1
                continue
            new_videos.append(video)
        new_videos.sort(key=lambda video: video.create_time)
        logger.info(
            "[Profile %s] KẾT QUẢ QUÉT DOUYIN: kiểm tra %s video | MỚI %s | "
            "đã xử lý %s | loại do thiếu lượt thích %s | loại do quá dài %s.",
            self.profile_id,
            len(videos),
            len(new_videos),
            already_seen_count,
            below_likes_count,
            over_duration_count,
        )
        if new_videos:
            console.print(
                f"[bold green]🆕 [Profile {self.profile_id}] "
                f"Phát hiện {len(new_videos)} video mới![/]"
            )
        return new_videos

    def build_start_baseline(
        self,
        latest_count: int | None = None,
    ) -> list[DouyinVideo] | None:
        videos = self.fetch_latest_videos(pages_to_fetch=1)
        if not self.last_scan_succeeded:
            return None
        newest_videos = sorted(
            videos,
            key=lambda video: video.create_time,
            reverse=True,
        )
        if latest_count is not None:
            newest_videos = newest_videos[:latest_count]
        max_create_time = max(
            (video.create_time for video in newest_videos),
            default=0,
        )
        self.state.replace_seen(
            [video.aweme_id for video in newest_videos],
            max_create_time,
        )
        return newest_videos

    def mark_processed(self, video: DouyinVideo) -> None:
        self.state.mark_seen(video.aweme_id, video.create_time)

    def mark_ignored(self, video: DouyinVideo) -> None:
        self.state.mark_seen(video.aweme_id, video.create_time)
