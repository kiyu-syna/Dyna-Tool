import os
import re
import time
from contextlib import ExitStack
from pathlib import Path

from profile_automation.uploaders.base_uploader import BaseUploader, UploadSkipped, UploadUnconfirmed
from profile_automation.uploaders.upload_log import log_upload_section
from profile_automation.watchers.douyin_video import DouyinVideo
from core.utils import logger
from services.integrations.intervention_notifier import notify_intervention
from profile_automation.browser_utils import is_captcha_present, set_video_file_background
import core.config as config
from services.browser.browser_profile_service import (
    browser_profile_label,
    connected_browser_profile as connected_gemlogin_profile,
    create_background_page,
)
from services.integrations.diagnostic_artifact_service import attach_response_trace, record_browser_diagnostic

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
CLICK_UPLOAD_X, CLICK_UPLOAD_Y = 1117, 835


HASHTAG_PATTERN = re.compile(r"#\S+")
PUBLISH_SUCCESS_TEXTS = (
    "Your video is being uploaded to TikTok",
    "Your video has been uploaded",
    "Video uploaded",
    "Upload another video",
    "Manage posts",
    "Video của bạn đang được tải lên TikTok",
    "Đã tải video lên",
    "Tải video khác lên",
    "Quản lý bài đăng",
    "Your video has been published",
    "Video đã được đăng",
    "Video has been published",
    "Post another video",
    "Đăng video khác",
    "View profile",
    "Xem hồ sơ",
    "Manage your posts",
    "Quản lý bài viết",
    "Video của bạn đang được xử lý",
    "Your video is being processed",
    "Video đã tải lên",
    "Uploaded",
    "Published",
    "Đã xuất bản",
    "Đã đăng",
    "Bài viết của bạn đang được xử lý",
)


def _close_page_quietly(page) -> None:
    if page is None:
        return
    try:
        if not page.is_closed():
            page.close()
    except Exception:
        pass


def _publish_confirmation_signal(page) -> str:
    for text in PUBLISH_SUCCESS_TEXTS:
        try:
            if page.get_by_text(text, exact=False).first.is_visible(timeout=800):
                return f'text="{text}"'
        except Exception:
            continue
    try:
        current_url = str(page.url or "")
    except Exception:
        current_url = ""
    if any(
        marker in current_url.casefold()
        for marker in (
            "/tiktokstudio/content",
            "/creator-center/content",
            "/tiktokstudio/posts",
            "/creator-center/posts",
            "/creator_center",
        )
    ) or (current_url and "/upload" not in current_url.casefold() and "tiktokstudio" in current_url.casefold()):
        return f'url="{current_url}"'

    try:
        toasts = page.locator('div[class*="toast"]:visible, div[role="alert"]:visible')
        for index in range(min(toasts.count(), 5)):
            txt = toasts.nth(index).inner_text(timeout=200).strip()
            if any(k in txt.casefold() for k in ("đã đăng", "published", "uploaded", "đang được", "processing", "thành công", "success", "quản lý", "manage")):
                return f'toast="{txt[:60]}"'
    except Exception:
        pass

    try:
        modal = page.locator('div[role="dialog"]:has-text("Manage"), div[role="dialog"]:has-text("Quản lý"), div[role="dialog"]:has-text("Upload"), div[role="dialog"]:has-text("Tải lên")')
        if modal.count() > 0 and modal.first.is_visible():
            return "modal_completed"
    except Exception:
        pass

    return ""


def _wait_for_publish_confirmation(page, timeout_seconds: float = 45) -> str:
    deadline = time.monotonic() + max(0, float(timeout_seconds))
    while True:
        signal = _publish_confirmation_signal(page)
        if signal:
            return signal
        if time.monotonic() >= deadline:
            return ""
        time.sleep(2)

def _type_caption_with_tiktok_hashtags(page, caption_text: str):
    text = caption_text or ""
    cursor = 0
    for match in HASHTAG_PATTERN.finditer(text):
        prefix = text[cursor:match.start()]
        hashtag = match.group(0)
        if prefix:
            page.keyboard.type(prefix, delay=50)
        page.keyboard.type(hashtag, delay=50)
        time.sleep(0.2)
        page.keyboard.press("Space")
        time.sleep(0.2)
        page.keyboard.press("Backspace")
        time.sleep(2.0)
        page.keyboard.press("Tab")
        time.sleep(0.2)
        cursor = match.end()
    suffix = text[cursor:]
    if suffix:
        page.keyboard.type(suffix, delay=50)

class TikTokUploader(BaseUploader):
    def upload(self, video_path: str, video: DouyinVideo, profile: dict) -> bool:
        profile_id = str(profile.get("id", "1"))
        video_id = str(video.aweme_id)
        tiktok_cfg = profile.get("tiktok", {})
        gemlogin_profile_id = tiktok_cfg.get("gemlogin_profile_id")
        browser_label = browser_profile_label(gemlogin_profile_id, profile)
        started_at = time.monotonic()
        file_name = Path(video_path).name
        try:
            file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        except OSError:
            file_size_mb = 0

        def progress(step: int, message: str, *args, level: str = "info") -> None:
            getattr(logger, level)(
                f"[Đăng TikTok][Profile %s][Video %s][Bước {step}/7] {message}",
                profile_id,
                video_id,
                *args,
            )

        log_upload_section("TikTok", profile_id, video_id, "Bắt đầu")
        logger.info(
            "[Đăng TikTok][Profile %s][Video %s] THÔNG TIN | tệp=%s | dung lượng=%.1f MB | trình duyệt=%s",
            profile_id,
            video_id,
            file_name,
            file_size_mb,
            browser_label,
        )
        api_url = config.API_URL
        is_safe = False
        restriction_detected = False
        upload_succeeded = False
        publish_confirmation = ""
        page = None
        response_trace: dict = {}
        diagnostic_recorded = False

        try:
            progress(1, "Kiểm tra tệp đầu vào: %s (%.1f MB).", file_name, file_size_mb)
            if not os.path.isfile(video_path):
                raise FileNotFoundError(video_path)

            progress(2, "Đang kết nối Chromium %s.", browser_label)
            with connected_gemlogin_profile(
                gemlogin_profile_id,
                api_url,
                profile_config=profile,
            ) as browser, ExitStack() as page_cleanup:
                progress(2, "Đã kết nối Chromium thành công.")
                if not browser.contexts:
                    raise RuntimeError("Không tìm thấy phiên trình duyệt sau khi kết nối CDP.")
                context = browser.contexts[0]
                try:
                    progress(3, "Đang tạo tab nền và mở TikTok Studio.")
                    page = create_background_page(browser, context)
                    page_cleanup.callback(_close_page_quietly, page)
                    response_trace = attach_response_trace(page)
                    page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
                    time.sleep(5)
                    progress(3, "TikTok Studio đã tải xong.")

                    progress(4, "Đang kiểm tra CAPTCHA.")
                    if is_captcha_present(page):
                        progress(4, "Phát hiện CAPTCHA; đang chờ người dùng xử lý.", level="warning")
                        notify_intervention(
                            "Phát hiện CAPTCHA trên TikTok Studio. Vui lòng xử lý để tiếp tục upload.",
                            page,
                        )
                        captcha_wait = 0
                        while is_captcha_present(page):
                            time.sleep(5)
                            captcha_wait += 5
                            if captcha_wait % 30 == 0:
                                progress(4, "Vẫn đang chờ xử lý CAPTCHA (%s giây).", captcha_wait)
                        progress(4, "CAPTCHA đã được xử lý; tiếp tục đăng.")
                    else:
                        progress(4, "Không phát hiện CAPTCHA.")

                    try:
                        client = page.context.new_cdp_session(page)
                        client.send("Browser.setDownloadBehavior", {"behavior": "default"})
                    except Exception as exc:
                        progress(5, "Không thiết lập được chế độ tải tệp: %s", exc, level="warning")

                    def trigger_tiktok_file_chooser():
                        for selector in (
                            'div.Button__content:has-text("Select video")',
                            'button:has-text("Select video")',
                            'text="Select video"',
                        ):
                            try:
                                button = page.locator(selector).first
                                if button.is_visible(timeout=3000):
                                    button.click(force=True)
                                    return
                            except Exception:
                                continue
                        page.mouse.click(CLICK_UPLOAD_X, CLICK_UPLOAD_Y)

                    progress(5, "Đang đưa tệp video vào TikTok Studio.")
                    set_video_file_background(page, video_path, trigger_tiktok_file_chooser)
                    progress(5, "TikTok Studio đã nhận tệp; chờ giao diện xử lý video.")
                    time.sleep(10)

                    caption_box = None
                    caption_selector = ""
                    for selector in (
                        'div[class*="notranslate"][contenteditable="true"]',
                        'div[data-contents="true"]',
                        ".public-DraftEditor-content",
                    ):
                        try:
                            caption_box = page.wait_for_selector(selector, timeout=5000)
                            if caption_box:
                                caption_selector = selector
                                break
                        except Exception:
                            continue

                    if caption_box:
                        caption_box.click()
                        time.sleep(1)
                        page.keyboard.press("Control+A")
                        page.keyboard.press("Backspace")
                        time.sleep(1)
                        use_original = bool(
                            profile.get("_runtime_use_original_desc", False)
                            or tiktok_cfg.get("use_original_desc", False)
                        )
                        desc_text = str(
                            video.desc
                            if use_original
                            else profile.get("_runtime_caption") or video.desc or ""
                        ).strip()
                        _type_caption_with_tiktok_hashtags(page, desc_text)
                        page.mouse.click(10, 10)
                        time.sleep(1)
                        progress(
                            6,
                            "Đã nhập mô tả (%s ký tự, nguồn=%s, ô nhập=%s).",
                            len(desc_text),
                            "mô tả gốc" if use_original else "mô tả cấu hình",
                            caption_selector,
                        )
                    else:
                        progress(6, "Không tìm thấy ô mô tả; giữ nguyên nội dung trên giao diện.", level="warning")

                    progress(6, "Đang chờ TikTok kiểm tra nội dung/bản quyền.")
                    page.mouse.click(475, 579)
                    time.sleep(1)
                    page.keyboard.press("End")
                    time.sleep(8)

                    wait_check = 0
                    while wait_check < 600:
                        safe_vn = "Không phát hiện vấn đề nào. Tuy nhiên, video của bạn vẫn có thể bị xóa sau này"
                        safe_en = "No issues found. However, your video could still be removed later"
                        fail_vn = "Nội dung có thể sẽ bị hạn chế. Bạn vẫn có thể đăng bài"
                        fail_en = "Content may be restricted. You can still post"
                        safe_el = page.get_by_text(safe_vn).or_(page.get_by_text(safe_en)).first
                        fail_el = page.get_by_text(fail_vn).or_(page.get_by_text(fail_en)).first

                        if safe_el.is_visible():
                            is_safe = True
                            progress(6, "TikTok xác nhận không phát hiện vấn đề nội dung.")
                            break
                        if fail_el.is_visible():
                            restriction_detected = True
                            progress(6, "TikTok cảnh báo nội dung có thể bị hạn chế.", level="warning")
                            break
                        if wait_check % 30 == 0:
                            progress(6, "Vẫn đang chờ kiểm tra nội dung (%s/600 giây).", wait_check)
                        time.sleep(5)
                        wait_check += 5

                    if restriction_detected:
                        raise UploadSkipped("TikTok báo nội dung có thể bị hạn chế; đã bỏ qua video.")
                    if not is_safe:
                        raise RuntimeError("TikTok không hoàn tất kiểm tra nội dung trong 600 giây.")

                    progress(7, "Đang tìm và chờ nút Đăng sẵn sàng.")
                    post_btn = page.locator(
                        'button:has-text("Post"), button:has-text("Đăng")'
                    ).last
                    try:
                        post_btn.scroll_into_view_if_needed()
                    except Exception as exc:
                        progress(7, "Không cuộn được đến nút Đăng: %s", exc, level="warning")

                    wait_post = 0
                    while wait_post < 300 and not post_btn.is_enabled():
                        time.sleep(2)
                        wait_post += 2
                        if wait_post % 30 == 0:
                            progress(7, "Nút Đăng chưa sẵn sàng (%s/300 giây).", wait_post)
                    if not post_btn.is_enabled():
                        raise RuntimeError("Nút Đăng không sẵn sàng sau 300 giây.")

                    post_btn.click(force=True)
                    progress(7, "Đã nhấn Đăng; đang chờ TikTok xác nhận.")
                    publish_confirmation = _wait_for_publish_confirmation(page)
                    if publish_confirmation:
                        progress(
                            7,
                            "TikTok đã xác nhận yêu cầu đăng (%s).",
                            publish_confirmation,
                        )
                    else:
                        progress(
                            7,
                            "Không đọc được thông báo xác nhận sau 45 giây; yêu cầu đăng đã được gửi.",
                            level="warning",
                        )
                        record_browser_diagnostic(
                            page=page,
                            profile_id=profile_id,
                            video_id=video_id,
                            platform="tiktok",
                            error=RuntimeError("TikTok không xác nhận đăng trong 45 giây."),
                            url=UPLOAD_URL,
                            last_response=response_trace,
                        )
                        diagnostic_recorded = True
                    upload_succeeded = True
                except UploadSkipped:
                    raise
                except Exception as exc:
                    record_browser_diagnostic(
                        page=page,
                        profile_id=profile_id,
                        video_id=video_id,
                        platform="tiktok",
                        error=exc,
                        url=UPLOAD_URL,
                        last_response=response_trace,
                    )
                    diagnostic_recorded = True
                    raise
                finally:
                    _close_page_quietly(page)
        except UploadSkipped as exc:
            logger.warning(
                "[Đăng TikTok][Profile %s][Video %s] BỎ QUA sau %.1f giây | lý do=%s",
                profile_id,
                video_id,
                time.monotonic() - started_at,
                exc,
            )
            log_upload_section(
                "TikTok", profile_id, video_id, "Kết thúc", status="Bỏ qua", level="warning"
            )
            raise
        except Exception as exc:
            logger.error(
                "[Đăng TikTok][Profile %s][Video %s] THẤT BẠI sau %.1f giây | lỗi=%s",
                profile_id,
                video_id,
                time.monotonic() - started_at,
                exc,
            )
            if not diagnostic_recorded:
                record_browser_diagnostic(
                    page=page,
                    profile_id=profile_id,
                    video_id=video_id,
                    platform="tiktok",
                    error=exc,
                    url=UPLOAD_URL,
                    last_response=response_trace,
                )
            log_upload_section(
                "TikTok", profile_id, video_id, "Kết thúc", status="Thất bại", level="error"
            )
            return False

        if publish_confirmation:
            logger.info(
                "[Đăng TikTok][Profile %s][Video %s] HOÀN TẤT THÀNH CÔNG sau %.1f giây | xác nhận=%s",
                profile_id,
                video_id,
                time.monotonic() - started_at,
                publish_confirmation,
            )
        else:
            logger.warning(
                "[Đăng TikTok][Profile %s][Video %s] ĐÃ GỬI YÊU CẦU ĐĂNG sau %.1f giây | chưa đọc được xác nhận từ TikTok.",
                profile_id,
                video_id,
                time.monotonic() - started_at,
            )
        log_upload_section(
            "TikTok",
            profile_id,
            video_id,
            "Kết thúc",
            status="Thành công" if publish_confirmation else "Chưa xác nhận",
            level="info" if publish_confirmation else "warning",
        )
        if upload_succeeded and not publish_confirmation:
            raise UploadUnconfirmed(
                "TikTok đã nhận thao tác Đăng nhưng không xác nhận trong 45 giây; "
                "chưa thể kết luận video đã được đăng."
            )
        return upload_succeeded
