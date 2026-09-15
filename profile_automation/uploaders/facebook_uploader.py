import os
import time
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Locator, Page

import core.config as config
from core.utils import logger
from profile_automation.browser_utils import set_video_file_background
from profile_automation.uploaders.base_uploader import BaseUploader, UploadUnconfirmed
from profile_automation.uploaders.upload_log import log_upload_section
from profile_automation.watchers.douyin_video import DouyinVideo
from services.browser.browser_profile_service import (
    browser_profile_label,
    connected_browser_profile as connected_gemlogin_profile,
    create_background_page,
)
from services.integrations.diagnostic_artifact_service import attach_response_trace, record_browser_diagnostic


DEFAULT_PROFILE_URL = "https://www.facebook.com/me"
FACEBOOK_PUBLISH_SUCCESS_TEXTS = (
    "Your reel is being published",
    "Your reel was published",
    "Reel published",
    "View reel",
    "Thước phim của bạn đang được đăng",
    "Đã đăng thước phim",
    "Xem thước phim",
)


def _close_page_quietly(page) -> None:
    if page is None:
        return
    try:
        if not page.is_closed():
            page.close()
    except Exception:
        pass


def _build_facebook_caption(video: DouyinVideo, profile: dict) -> str:
    facebook_cfg = profile.get("facebook", {})
    if profile.get("_runtime_use_original_desc", False) or facebook_cfg.get("use_original_desc", False):
        caption = str(video.desc or "").strip()
    else:
        caption = str(profile.get("_runtime_caption") or video.desc or "").strip()

    return caption


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _find_visible_aria_button(page: Page, labels: tuple[str, ...], timeout_ms: int) -> Locator:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for label in labels:
            buttons = page.locator(f'[role="button"][aria-label="{label}"]')
            for index in range(buttons.count()):
                button = buttons.nth(index)
                try:
                    if button.is_visible() and button.get_attribute("aria-disabled") != "true":
                        return button
                except Exception:
                    continue
        time.sleep(0.5)
    raise TimeoutError(f"Không tìm thấy nút đang hoạt động: {', '.join(labels)}")


def _click_aria_button(page: Page, labels: tuple[str, ...], timeout_ms: int = 60000) -> None:
    button = _find_visible_aria_button(page, labels, timeout_ms)
    button.scroll_into_view_if_needed()
    button.click(force=True)


def _click_visible_text_button(
    page: Page,
    labels: tuple[str, ...],
    timeout_ms: int = 60000,
    required: bool = True,
) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for label in labels:
            matches = page.get_by_text(label, exact=True)
            for index in range(matches.count()):
                text_element = matches.nth(index)
                try:
                    if not text_element.is_visible():
                        continue
                    clickable = text_element.locator(
                        "xpath=ancestor::*[@role='button' or @tabindex='0'][1]"
                    )
                    target = clickable.first if clickable.count() else text_element
                    if target.get_attribute("aria-disabled") == "true":
                        continue
                    target.scroll_into_view_if_needed()
                    target.click(force=True)
                    return True
                except Exception:
                    continue
        time.sleep(0.5)
    if required:
        raise TimeoutError(f"Không tìm thấy nút đang hoạt động: {', '.join(labels)}")
    return False


def _has_visible_match(locator: Locator) -> bool:
    for index in range(locator.count()):
        try:
            if locator.nth(index).is_visible():
                return True
        except Exception:
            continue
    return False


def _has_enabled_visible_aria_button(page: Page, labels: tuple[str, ...]) -> bool:
    for label in labels:
        buttons = page.locator(f'[role="button"][aria-label="{label}"]')
        for index in range(buttons.count()):
            try:
                button = buttons.nth(index)
                if button.is_visible() and button.get_attribute("aria-disabled") != "true":
                    return True
            except Exception:
                continue
    return False


def _facebook_publish_confirmation_signal(page: Page) -> str:
    for text in FACEBOOK_PUBLISH_SUCCESS_TEXTS:
        try:
            if _has_visible_match(page.get_by_text(text, exact=False)):
                return f'text="{text}"'
        except Exception:
            continue
    try:
        current_url = str(page.url or "")
    except Exception:
        current_url = ""
    if "/reel/" in current_url.casefold():
        return f'url="{current_url}"'
    return ""


def _wait_for_facebook_publish_confirmation(
    page: Page,
    timeout_seconds: float = 45,
) -> str:
    deadline = time.monotonic() + max(0, float(timeout_seconds))
    while True:
        signal = _facebook_publish_confirmation_signal(page)
        if signal:
            return signal
        if time.monotonic() >= deadline:
            return ""
        time.sleep(2)


def _wait_for_upload_complete(
    page: Page,
    timeout_seconds: int = 900,
    on_progress=None,
) -> None:
    complete = page.locator(
        'div:has(> span > i[aria-label="Đã tải lên xong"]):has-text("100%"), '
        'div:has(> span > i[aria-label="Upload complete"]):has-text("100%")'
    )
    deadline = time.monotonic() + timeout_seconds
    last_log_at = 0.0
    while time.monotonic() < deadline:
        try:
            if _has_visible_match(complete):
                return
        except Exception:
            pass
        now = time.monotonic()
        if on_progress is not None and now - last_log_at >= 30:
            elapsed = int(timeout_seconds - max(0, deadline - now))
            on_progress(elapsed, timeout_seconds)
            last_log_at = now
        time.sleep(1)
    raise TimeoutError("Facebook không báo tải video xong 100% trong thời gian chờ.")


def _wait_for_reel_safe(
    page: Page,
    timeout_seconds: int = 900,
    on_progress=None,
) -> None:
    safe_messages = page.get_by_text(
        "Thước phim của bạn an toàn để đăng!", exact=False
    ).or_(page.get_by_text("Your reel is safe to publish", exact=False))
    deadline = time.monotonic() + timeout_seconds
    last_log_at = 0.0
    while time.monotonic() < deadline:
        try:
            if _has_visible_match(safe_messages):
                return
            # Facebook does not consistently render the old "safe to publish"
            # sentence anymore. An enabled Next button is the stable UI signal
            # that the upload/processing gate has finished and the reel can move
            # to the next composer screen.
            if _has_enabled_visible_aria_button(page, ("Tiếp", "Next")):
                return
        except Exception:
            pass
        now = time.monotonic()
        if on_progress is not None and now - last_log_at >= 30:
            elapsed = int(timeout_seconds - max(0, deadline - now))
            on_progress(elapsed, timeout_seconds)
            last_log_at = now
        time.sleep(1)
    raise TimeoutError("Facebook không xác nhận thước phim an toàn trong thời gian chờ.")


class FacebookUploader(BaseUploader):
    def upload(self, video_path: str, video: DouyinVideo, profile: dict) -> bool:
        profile_id = str(profile.get("id", "1"))
        video_id = str(video.aweme_id)
        facebook_cfg = profile.get("facebook", {})
        gemlogin_profile_id = str(
            facebook_cfg.get("gemlogin_profile_id") or profile_id
        ).strip()
        profile_url = str(
            facebook_cfg.get("profile_url") or DEFAULT_PROFILE_URL
        ).strip()
        browser_label = browser_profile_label(gemlogin_profile_id, profile)
        started_at = time.monotonic()
        file_name = Path(video_path).name
        try:
            file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        except OSError:
            file_size_mb = 0

        def progress(step: int, message: str, *args, level: str = "info") -> None:
            getattr(logger, level)(
                f"[Đăng Facebook][Profile %s][Video %s][Bước {step}/7] {message}",
                profile_id,
                video_id,
                *args,
            )

        log_upload_section("Facebook", profile_id, video_id, "Bắt đầu")
        logger.info(
            "[Đăng Facebook][Profile %s][Video %s] THÔNG TIN | tệp=%s | dung lượng=%.1f MB | trình duyệt=%s",
            profile_id,
            video_id,
            file_name,
            file_size_mb,
            browser_label,
        )

        progress(1, "Kiểm tra tệp và cấu hình trang Facebook.")
        if not os.path.isfile(video_path):
            logger.error(
                "[Đăng Facebook][Profile %s][Video %s] THẤT BẠI | không tìm thấy tệp=%s",
                profile_id,
                video_id,
                video_path,
            )
            log_upload_section(
                "Facebook", profile_id, video_id, "Kết thúc", status="Thất bại", level="error"
            )
            return False
        if not _is_http_url(profile_url):
            logger.error(
                "[Đăng Facebook][Profile %s][Video %s] THẤT BẠI | URL không hợp lệ=%s",
                profile_id,
                video_id,
                profile_url,
            )
            log_upload_section(
                "Facebook", profile_id, video_id, "Kết thúc", status="Thất bại", level="error"
            )
            return False

        page = None
        response_trace: dict = {}
        upload_succeeded = False
        publish_confirmation = ""
        try:
            progress(2, "Đang kết nối Chromium %s.", browser_label)
            with connected_gemlogin_profile(
                gemlogin_profile_id,
                config.API_URL,
                profile_config=profile,
            ) as browser, ExitStack() as page_cleanup:
                progress(2, "Đã kết nối Chromium thành công.")
                if not browser.contexts:
                    raise RuntimeError("Không tìm thấy phiên trình duyệt sau khi kết nối CDP.")

                context = browser.contexts[0]
                progress(3, "Đang tạo tab nền và mở trang Facebook: %s", profile_url)
                page = create_background_page(browser, context)
                page_cleanup.callback(_close_page_quietly, page)
                response_trace = attach_response_trace(page)
                page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
                progress(3, "Trang Facebook đã tải xong.")

                progress(4, "Đang đưa tệp video vào trình soạn Facebook Reels.")
                set_video_file_background(
                    page,
                    video_path,
                    lambda: _click_aria_button(page, ("Ảnh/video", "Photo/video"), timeout_ms=60000),
                )
                progress(4, "Facebook đã nhận tệp; đang chờ tải lên 100%%.")

                _wait_for_upload_complete(
                    page,
                    on_progress=lambda elapsed, timeout: progress(
                        4,
                        "Vẫn đang tải video (%s/%s giây).",
                        elapsed,
                        timeout,
                    ),
                )
                progress(4, "Facebook xác nhận tải video xong 100%%.")

                progress(5, "Đang chờ Facebook xử lý và kiểm tra thước phim.")
                _wait_for_reel_safe(
                    page,
                    on_progress=lambda elapsed, timeout: progress(
                        5,
                        "Vẫn đang chờ kiểm tra thước phim (%s/%s giây).",
                        elapsed,
                        timeout,
                    ),
                )
                progress(5, "Facebook cho phép chuyển sang bước tiếp theo.")

                for step in range(2):
                    _click_aria_button(page, ("Tiếp", "Next"), timeout_ms=60000)
                    progress(6, "Đã nhấn Tiếp %s/2.", step + 1)
                    time.sleep(2)

                caption_box = page.locator(
                    'div[contenteditable="true"][role="textbox"]'
                    '[aria-placeholder="Mô tả thước phim của bạn..."]:visible, '
                    'div[contenteditable="true"][role="textbox"]'
                    '[aria-placeholder="Describe your reel..."]:visible'
                ).first
                caption_box.wait_for(state="visible", timeout=60000)
                caption_box.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                caption = _build_facebook_caption(video, profile)
                if caption:
                    page.keyboard.insert_text(caption)
                use_original = bool(
                    profile.get("_runtime_use_original_desc", False)
                    or facebook_cfg.get("use_original_desc", False)
                )
                progress(
                    6,
                    "Đã nhập mô tả (%s ký tự, nguồn=%s).",
                    len(caption),
                    "mô tả gốc" if use_original else "mô tả cấu hình",
                )

                progress(7, "Đang tìm và nhấn nút Đăng Facebook Reels.")
                _click_aria_button(page, ("Đăng", "Post", "Publish"), timeout_ms=60000)
                progress(7, "Đã nhấn Đăng; đang xử lý hộp thoại sau đăng.")
                dismissed_later_prompt = _click_visible_text_button(
                    page,
                    ("Lúc khác", "Not now", "Maybe later"),
                    timeout_ms=10000,
                    required=False,
                )
                if dismissed_later_prompt:
                    progress(7, "Đã đóng lời nhắc bằng nút Lúc khác.")
                else:
                    progress(7, "Facebook không hiện lời nhắc Lúc khác; tiếp tục xác nhận.")
                publish_confirmation = _wait_for_facebook_publish_confirmation(page)
                if publish_confirmation:
                    progress(7, "Facebook đã xác nhận đăng (%s).", publish_confirmation)
                else:
                    progress(
                        7,
                        "Không đọc được thông báo xác nhận sau 45 giây; lệnh Đăng đã được gửi.",
                        level="warning",
                    )
                    record_browser_diagnostic(
                        page=page,
                        profile_id=profile_id,
                        video_id=video_id,
                        platform="facebook",
                        error=RuntimeError("Facebook không xác nhận đăng trong 45 giây."),
                        url=profile_url,
                        last_response=response_trace,
                    )
                upload_succeeded = True

        except Exception as exc:
            logger.exception(
                "[Đăng Facebook][Profile %s][Video %s] THẤT BẠI sau %.1f giây | lỗi=%s",
                profile_id,
                video_id,
                time.monotonic() - started_at,
                exc,
            )
            record_browser_diagnostic(
                page=page,
                profile_id=profile_id,
                video_id=str(video.aweme_id),
                platform="facebook",
                error=exc,
                url=profile_url,
                last_response=response_trace,
            )
        finally:
            if page is not None:
                _close_page_quietly(page)

        if upload_succeeded and publish_confirmation:
            logger.info(
                "[Đăng Facebook][Profile %s][Video %s] HOÀN TẤT THÀNH CÔNG sau %.1f giây | xác nhận=%s",
                profile_id,
                video_id,
                time.monotonic() - started_at,
                publish_confirmation,
            )
        elif upload_succeeded:
            logger.warning(
                "[Đăng Facebook][Profile %s][Video %s] HOÀN TẤT GỬI ĐĂNG sau %.1f giây | chưa đọc được xác nhận hiển thị từ Facebook.",
                profile_id,
                video_id,
                time.monotonic() - started_at,
            )
        log_upload_section(
            "Facebook",
            profile_id,
            video_id,
            "Kết thúc",
            status=(
                "Thành công"
                if upload_succeeded and publish_confirmation
                else "Chưa xác nhận"
                if upload_succeeded
                else "Thất bại"
            ),
            level=(
                "info"
                if upload_succeeded and publish_confirmation
                else "warning"
                if upload_succeeded
                else "error"
            ),
        )
        if upload_succeeded and not publish_confirmation:
            raise UploadUnconfirmed(
                "Facebook đã nhận thao tác Đăng nhưng không xác nhận trong 45 giây; "
                "chưa thể kết luận video đã được đăng."
            )
        return upload_succeeded
