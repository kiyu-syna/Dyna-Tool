import os
import time
from contextlib import ExitStack
from urllib.parse import urlparse

from playwright.sync_api import Locator, Page

import core.config as config
from core.utils import console, logger
from profile_automation.browser_utils import set_video_file_background
from profile_automation.uploaders.base_uploader import BaseUploader
from profile_automation.watchers.douyin_video import DouyinVideo
from services.browser.browser_profile_service import (
    browser_profile_label,
    connected_browser_profile as connected_gemlogin_profile,
    create_background_page,
)
from services.integrations.diagnostic_artifact_service import attach_response_trace, record_browser_diagnostic


DEFAULT_PROFILE_URL = "https://www.facebook.com/me"
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


def _wait_for_upload_complete(page: Page, timeout_seconds: int = 900) -> None:
    complete = page.locator(
        'div:has(> span > i[aria-label="Đã tải lên xong"]):has-text("100%"), '
        'div:has(> span > i[aria-label="Upload complete"]):has-text("100%")'
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if _has_visible_match(complete):
                return
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError("Facebook không báo tải video xong 100% trong thời gian chờ.")


def _wait_for_reel_safe(page: Page, timeout_seconds: int = 900) -> None:
    safe_messages = page.get_by_text(
        "Thước phim của bạn an toàn để đăng!", exact=False
    ).or_(page.get_by_text("Your reel is safe to publish", exact=False))
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if _has_visible_match(safe_messages):
                return
            # Facebook does not consistently render the old "safe to publish"
            # sentence anymore. An enabled Next button is the stable UI signal
            # that the upload/processing gate has finished and the reel can move
            # to the next composer screen.
            if _has_enabled_visible_aria_button(page, ("Tiếp", "Next")):
                logger.info(
                    "Facebook đã bật nút Tiếp; tiếp tục dù không hiện thông báo "
                    "thước phim an toàn."
                )
                return
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError("Facebook không xác nhận thước phim an toàn trong thời gian chờ.")


class FacebookUploader(BaseUploader):
    def upload(self, video_path: str, video: DouyinVideo, profile: dict) -> bool:
        profile_id = str(profile.get("id", "1"))
        facebook_cfg = profile.get("facebook", {})
        gemlogin_profile_id = str(
            facebook_cfg.get("gemlogin_profile_id") or profile_id
        ).strip()
        profile_url = str(
            facebook_cfg.get("profile_url") or DEFAULT_PROFILE_URL
        ).strip()

        if not os.path.isfile(video_path):
            logger.error("[Profile %s] Không tìm thấy video Facebook: %s", profile_id, video_path)
            return False
        if not _is_http_url(profile_url):
            logger.error("[Profile %s] URL trang Facebook không hợp lệ: %s", profile_id, profile_url)
            return False

        logger.info("==========================================================")
        logger.info(
            "BẮT ĐẦU ĐĂNG FACEBOOK REELS - PROFILE %s (%s)",
            profile_id,
            browser_profile_label(gemlogin_profile_id, profile),
        )
        logger.info("==========================================================")

        page = None
        response_trace: dict = {}
        upload_succeeded = False
        try:
            with connected_gemlogin_profile(
                gemlogin_profile_id,
                config.API_URL,
                profile_config=profile,
            ) as browser, ExitStack() as page_cleanup:
                if not browser.contexts:
                    logger.error("[Profile %s] Trình duyệt không có context.", profile_id)
                    return False

                context = browser.contexts[0]
                page = create_background_page(browser, context)
                page_cleanup.callback(_close_page_quietly, page)
                response_trace = attach_response_trace(page)
                logger.info("[Profile %s] Mở trang Facebook: %s", profile_id, profile_url)
                page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)

                set_video_file_background(
                    page,
                    video_path,
                    lambda: _click_aria_button(page, ("Ảnh/video", "Photo/video"), timeout_ms=60000),
                )
                logger.info("[Profile %s] Đã chọn video Facebook bằng Playwright ở chế độ nền.", profile_id)

                # Step 4: wait until Facebook confirms that the file reached 100%.
                console.print("   ➔ Đang chờ Facebook tải video lên 100%...")
                _wait_for_upload_complete(page)
                logger.info("[Profile %s] Facebook đã tải video lên 100%%.", profile_id)

                # Step 5: Facebook's safety scan must finish before continuing.
                console.print("   ➔ Đang chờ Facebook xác nhận thước phim an toàn...")
                _wait_for_reel_safe(page)

                # Steps 6-7: the two screens expose the same aria-label. Resolve the
                # currently visible/enabled button after each transition.
                for step in range(2):
                    _click_aria_button(page, ("Tiếp", "Next"), timeout_ms=60000)
                    logger.info("[Profile %s] Đã nhấn Tiếp %s/2.", profile_id, step + 1)
                    time.sleep(2)

                # Step 8: enter the caption on the Reel description screen, which
                # only appears after the second Next click.
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
                logger.info("[Profile %s] Đã nhập mô tả Facebook Reels.", profile_id)

                # Steps 9-10: publish, dismiss the scheduling prompt, then wait.
                _click_aria_button(page, ("Đăng", "Post", "Publish"), timeout_ms=60000)
                logger.info("[Profile %s] Đã nhấn Đăng Facebook Reels.", profile_id)
                dismissed_later_prompt = _click_visible_text_button(
                    page,
                    ("Lúc khác", "Not now", "Maybe later"),
                    timeout_ms=10000,
                    required=False,
                )
                if dismissed_later_prompt:
                    logger.info(
                        "[Profile %s] Đã nhấn Lúc khác sau khi đăng Facebook Reels.",
                        profile_id,
                    )
                else:
                    logger.info(
                        "[Profile %s] Facebook không hiện nút Lúc khác; tiếp tục hoàn tất.",
                        profile_id,
                    )
                time.sleep(10)
                upload_succeeded = True
                console.print("[bold green]✅ Facebook Reels đã hoàn tất luồng đăng.[/]")
                page.close()

        except Exception as exc:
            logger.exception("[Profile %s] Đăng Facebook Reels thất bại: %s", profile_id, exc)
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

        return upload_succeeded
