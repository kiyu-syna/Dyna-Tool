from __future__ import annotations

import json
import threading
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
    configure_lightweight_scan_page,
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
    content_type = str(headers.get("content-type") or "?")
    content_length = str(headers.get("content-length") or len(text))
    if not text.strip():
        raise ValueError(
            "Douyin tráº£ response rá»—ng "
            f"(HTTP {status}, content-type={content_type}, content-length={content_length})."
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        preview = " ".join(text[:160].split())
        raise ValueError(
            "Douyin tráº£ response khÃ´ng pháº£i JSON "
            f"(HTTP {status}, content-type={content_type}, body={preview!r})."
        ) from exc
    if not isinstance(data, dict):
        raise ValueError("Douyin tráº£ JSON khÃ´ng pháº£i object.")
    return data


class DouyinProfileMonitor:
    """Collect videos from one Douyin source through intercepted responses."""

    AWEME_POST_PATTERN = "/aweme/v1/web/aweme/post/"

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
        self._captured_responses: list[dict] = []
        self._capture_lock = threading.Lock()

    def _on_response(self, response: Response) -> None:
        if self.AWEME_POST_PATTERN not in response.url:
            return
        try:
            logger.info("[BẮT GÓI] Phát hiện response: %s", response.url[:120])
            text = response.text()
            if not text:
                return
            data = json.loads(text)
            aweme_list = data.get("aweme_list", [])
            logger.info(
                "[BẮT GÓI] status=%s, aweme=%s",
                data.get("status_code"),
                len(aweme_list),
            )
            if data.get("status_code") == 0 and "aweme_list" in data:
                with self._capture_lock:
                    self._captured_responses.append(data)
        except Exception as exc:
            logger.error("[LỖI BẮT GÓI] Không phân tích được JSON: %s", exc)

    def _capture_page(
        self,
        page,
        profile_url: str,
        page_count: int,
        timeout_per_page: float,
    ) -> tuple[Response, dict] | None:
        try:
            with page.expect_response(
                lambda response: self.AWEME_POST_PATTERN in response.url,
                timeout=int(timeout_per_page * 1000),
            ) as response_info:
                if page_count == 1:
                    try:
                        page.goto(profile_url, wait_until="commit", timeout=15000)
                    except Exception as exc:
                        logger.debug("[Profile %s] goto: %s", self.profile_id, exc)
                else:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            response = response_info.value
            return response, _decode_douyin_response(response)
        except Exception as exc:
            if is_gemlogin_connection_error(exc, self.profile_config):
                raise
            logger.warning(
                "[Profile %s] Không bắt được gói tin trang %s: %s",
                self.profile_id,
                page_count,
                exc,
            )
            return None

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
        all_videos: list[DouyinVideo] = []
        profile_url = f"https://www.douyin.com/user/{self.sec_uid}"

        with connected_gemlogin_profile(
            self.gemlogin_profile_id,
            self.api_url,
            profile_config=self.profile_config,
            resource_saving=True,
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
            configure_lightweight_scan_page(page)
            console.print(
                f"[cyan]🔍 [Profile {self.profile_id}] Mở profile: {profile_url}[/]"
            )

            try:
                page_count = 0
                while True:
                    page_count += 1
                    captured = self._capture_page(
                        page,
                        profile_url,
                        page_count,
                        timeout_per_page,
                    )
                    if captured is None:
                        break
                    response, data = captured
                    aweme_list = data.get("aweme_list", [])
                    if data.get("status_code") != 0 or not aweme_list:
                        logger.warning(
                            "[Profile %s] Response không có video.",
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
                    "[Profile %s] Lỗi fetch_latest_videos: %s",
                    self.profile_id,
                    exc,
                )
            finally:
                _close_page_quietly(page)
                logger.info("[Profile %s] Đã đóng tab quét Douyin.", self.profile_id)

        console.print(
            f"[green]   → Lấy được {len(all_videos)} video từ kênh nguồn[/]"
        )
        return all_videos[:MAX_AWEME_IDS_PER_SCAN]

    def get_new_videos(self) -> list[DouyinVideo]:
        is_first_run = not self.state.path.exists()
        if is_first_run:
            console.print(
                f"[yellow]⚠️ [Profile {self.profile_id}] Đang tạo baseline...[/]"
            )
            all_videos = self.fetch_latest_videos(pages_to_fetch=3)
            for video in all_videos:
                self.state.mark_seen(video.aweme_id, video.create_time)
            return []

        videos = self.fetch_latest_videos(pages_to_fetch=1)
        new_videos = [
            video
            for video in videos
            if self.state.is_new_video(video.aweme_id, video.create_time)
            and video.like_count >= self.min_likes
            and video.duration_seconds <= self.max_duration_sec
        ]
        new_videos.sort(key=lambda video: video.create_time)
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
        if not videos:
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
