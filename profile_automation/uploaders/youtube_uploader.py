import os
import re
import time
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import quote


import core.config as config
from core.utils import logger
from profile_automation.browser_utils import set_video_file_background
from profile_automation.uploaders.base_uploader import BaseUploader, UploadSkipped
from profile_automation.uploaders.upload_log import log_upload_section
from profile_automation.watchers.douyin_video import DouyinVideo
from services.publishing.youtube_shorts_converter import (
    DEFAULT_CRF,
    DEFAULT_PRESET,
    YouTubeShortsConversionError,
    convert_to_youtube_shorts,
)
from services.browser.browser_profile_service import (
    browser_profile_label,
    connected_browser_profile as connected_gemlogin_profile,
    create_background_page,
)
from services.integrations.diagnostic_artifact_service import attach_response_trace, record_browser_diagnostic


UPLOAD_URL = (
    "https://studio.youtube.com/channel/{channel_id}/videos/upload?d=ud&filter=%5B%5D"
    "&sort=%7B%22columnType%22%3A%22date%22%2C%22sortOrder%22%3A%22DESCENDING%22%7D"
)
HASHTAG_PATTERN = re.compile(r"#\S+")
YOUTUBE_CHECK_PENDING = "pending"
YOUTUBE_CHECK_COMPLETE = "complete"
YOUTUBE_CHECK_COPYRIGHT = "copyright"
YOUTUBE_CHECK_TIMEOUT = "timeout"
YOUTUBE_PUBLISH_SUCCESS_TEXTS = (
    "Video published",
    "Published",
    "Your video has been published",
    "Đã xuất bản video",
    "Video đã được xuất bản",
    "Đã xuất bản",
)


def _classify_youtube_check_status(labels) -> str:
    for label in labels:
        normalized = " ".join(str(label or "").split()).casefold()
        checks_complete = normalized.startswith("đã kiểm tra xong") or normalized.startswith(
            "checks complete"
        )
        if not checks_complete:
            continue
        copyright_detected = any(
            marker in normalized
            for marker in (
                "xác nhận quyền sở hữu",
                "bản quyền",
                "copyright",
            )
        )
        return YOUTUBE_CHECK_COPYRIGHT if copyright_detected else YOUTUBE_CHECK_COMPLETE
    return YOUTUBE_CHECK_PENDING


def _close_page_quietly(page) -> None:
    if page is None:
        return
    try:
        if not page.is_closed():
            page.close()
    except Exception:
        pass


def _youtube_publish_confirmation_signal(page) -> str:
    for text in YOUTUBE_PUBLISH_SUCCESS_TEXTS:
        try:
            locator = page.get_by_text(text, exact=False)
            for index in range(locator.count()):
                if locator.nth(index).is_visible():
                    return f'text="{text}"'
        except Exception:
            continue
    try:
        current_url = str(page.url or "")
    except Exception:
        current_url = ""
    if "/videos" in current_url.casefold() and "/videos/upload" not in current_url.casefold():
        return f'url="{current_url}"'
    return ""


def _wait_for_youtube_publish_confirmation(page, timeout_seconds: float = 45) -> str:
    deadline = time.monotonic() + max(0, float(timeout_seconds))
    while True:
        signal = _youtube_publish_confirmation_signal(page)
        if signal:
            return signal
        if time.monotonic() >= deadline:
            return ""
        time.sleep(2)


def _build_youtube_text(video_path: str, video: DouyinVideo, profile: dict) -> tuple[str, str]:
    youtube_cfg = profile.get("youtube", {})
    if profile.get("_runtime_use_original_desc", False) or youtube_cfg.get("use_original_desc", False):
        caption = str(video.desc or "").strip()
    else:
        caption = str(profile.get("_runtime_caption") or video.desc or "").strip()

    hashtags = HASHTAG_PATTERN.findall(caption)
    title = HASHTAG_PATTERN.sub("", caption)
    title = re.sub(r"\s+", " ", title).strip()
    if not title:
        title = Path(video_path).stem

    # YouTube title limit is 100 characters. Hashtags belong only in description.
    title = title[:100].rstrip()
    description = " ".join(dict.fromkeys(tag.strip() for tag in hashtags if tag.strip()))
    return title, description


def _wait_for_youtube_checks_complete(
    page,
    profile_id: str,
    video_id: str = "",
    timeout_seconds: int = 900,
) -> str:
    progress_labels = page.locator("span.progress-label.style-scope.ytcp-video-upload-progress")
    deadline = time.monotonic() + timeout_seconds
    last_text = ""
    last_log_at = 0.0

    while time.monotonic() < deadline:
        try:
            labels = progress_labels.all_inner_texts()
        except Exception as exc:
            logger.debug(
                "[Đăng YouTube][Profile %s][Video %s] Không đọc được trạng thái kiểm tra: %s",
                profile_id,
                video_id or "-",
                exc,
            )
            labels = []

        normalized_labels = [" ".join(label.split()) for label in labels]
        check_status = _classify_youtube_check_status(normalized_labels)
        if check_status == YOUTUBE_CHECK_COPYRIGHT:
            logger.warning(
                "[Đăng YouTube][Profile %s][Video %s][Bước 6/7] Phát hiện nội dung "
                "được xác nhận quyền sở hữu; hủy đăng video này.",
                profile_id,
                video_id or "-",
            )
            return YOUTUBE_CHECK_COPYRIGHT
        if check_status == YOUTUBE_CHECK_COMPLETE:
            logger.info(
                "[Đăng YouTube][Profile %s][Video %s][Bước 6/7] YouTube đã kiểm tra xong, không phát hiện vấn đề.",
                profile_id,
                video_id or "-",
            )
            return YOUTUBE_CHECK_COMPLETE

        visible_text = " | ".join(label for label in normalized_labels if label)
        if visible_text and visible_text != last_text:
            last_text = visible_text
            logger.info(
                "[Đăng YouTube][Profile %s][Video %s][Bước 6/7] Trạng thái kiểm tra: %s",
                profile_id,
                video_id or "-",
                visible_text,
            )

        now = time.monotonic()
        if now - last_log_at >= 30:
            elapsed = int(timeout_seconds - max(0, deadline - now))
            logger.info(
                "[Đăng YouTube][Profile %s][Video %s][Bước 6/7] Vẫn đang chờ kiểm tra (%s/%s giây).",
                profile_id,
                video_id or "-",
                elapsed,
                timeout_seconds,
            )
            last_log_at = now

        time.sleep(2)

    logger.error(
        "[Đăng YouTube][Profile %s][Video %s][Bước 6/7] Hết thời gian chờ kiểm tra. Trạng thái cuối=%s",
        profile_id,
        video_id or "-",
        last_text or "none",
    )
    return YOUTUBE_CHECK_TIMEOUT


def _select_public_visibility(page, timeout_seconds: int = 30) -> None:
    public_radios = page.locator(
        'tp-yt-paper-radio-button[name="PUBLIC"][aria-disabled="false"]'
    )
    deadline = time.monotonic() + timeout_seconds
    selected_radio = None

    while time.monotonic() < deadline and selected_radio is None:
        for index in range(public_radios.count()):
            candidate = public_radios.nth(index)
            try:
                if candidate.is_visible():
                    selected_radio = candidate
                    break
            except Exception:
                continue
        if selected_radio is None:
            time.sleep(0.5)

    if selected_radio is None:
        raise TimeoutError('Không tìm thấy lựa chọn YouTube name="PUBLIC".')

    if selected_radio.get_attribute("aria-checked") == "true":
        return

    radio_container = selected_radio.locator("#radioContainer")
    radio_container.wait_for(state="visible", timeout=timeout_seconds * 1000)
    radio_container.click(force=True)

    confirmation_deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < confirmation_deadline:
        if selected_radio.get_attribute("aria-checked") == "true":
            return
        time.sleep(0.25)

    raise TimeoutError("YouTube không xác nhận quyền hiển thị Công khai.")


class YouTubeUploader(BaseUploader):
    def upload(self, video_path: str, video: DouyinVideo, profile: dict) -> bool:
        profile_id = str(profile.get("id", "1"))
        video_id = str(video.aweme_id)
        youtube_cfg = profile.get("youtube", {})
        gemlogin_profile_id = str(youtube_cfg.get("gemlogin_profile_id") or profile_id).strip()
        channel_id = str(youtube_cfg.get("channel_id") or "").strip()
        preset = str(youtube_cfg.get("preset") or DEFAULT_PRESET).strip().lower()
        crf = youtube_cfg.get("crf", DEFAULT_CRF)
        browser_label = browser_profile_label(gemlogin_profile_id, profile)
        started_at = time.monotonic()
        source_name = Path(video_path).name
        try:
            source_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        except OSError:
            source_size_mb = 0

        def progress(step: int, message: str, *args, level: str = "info") -> None:
            getattr(logger, level)(
                f"[Đăng YouTube][Profile %s][Video %s][Bước {step}/7] {message}",
                profile_id,
                video_id,
                *args,
            )

        log_upload_section("YouTube", profile_id, video_id, "Bắt đầu")
        logger.info(
            "[Đăng YouTube][Profile %s][Video %s] THÔNG TIN | tệp=%s | dung lượng=%.1f MB | kênh=%s | trình duyệt=%s",
            profile_id,
            video_id,
            source_name,
            source_size_mb,
            channel_id or "chưa cấu hình",
            browser_label,
        )

        progress(1, "Kiểm tra tệp và cấu hình kênh YouTube.")
        if not os.path.isfile(video_path):
            logger.error(
                "[Đăng YouTube][Profile %s][Video %s] THẤT BẠI | không tìm thấy tệp=%s",
                profile_id,
                video_id,
                video_path,
            )
            log_upload_section(
                "YouTube", profile_id, video_id, "Kết thúc", status="Thất bại", level="error"
            )
            return False
        if not channel_id:
            logger.error(
                "[Đăng YouTube][Profile %s][Video %s] THẤT BẠI | chưa cấu hình mã kênh.",
                profile_id,
                video_id,
            )
            log_upload_section(
                "YouTube", profile_id, video_id, "Kết thúc", status="Thất bại", level="error"
            )
            return False

        converted_path = None
        upload_succeeded = False
        publish_confirmation = ""
        try:
            progress(2, "Đang chuyển đổi video sang YouTube Shorts (preset=%s, crf=%s).", preset, crf)
            converted_path = convert_to_youtube_shorts(
                video_path,
                preset=preset,
                crf=crf,
                profile_id=profile_id,
                video_id=str(video.aweme_id),
                priority=int(profile.get("_workload_priority", 100)),
                cancel_event=profile.get("_runtime_cancel_event"),
            )
            try:
                converted_size_mb = os.path.getsize(converted_path) / (1024 * 1024)
            except OSError:
                converted_size_mb = 0
            progress(
                2,
                "Chuyển đổi hoàn tất: %s (%.1f MB).",
                Path(converted_path).name,
                converted_size_mb,
            )
        except (YouTubeShortsConversionError, OSError, ValueError) as exc:
            logger.error(
                "[Đăng YouTube][Profile %s][Video %s] THẤT BẠI ở bước chuyển đổi sau %.1f giây | lỗi=%s",
                profile_id,
                video_id,
                time.monotonic() - started_at,
                exc,
            )
            record_browser_diagnostic(
                profile_id=profile_id,
                video_id=str(video.aweme_id),
                platform="youtube",
                error=exc,
                url=UPLOAD_URL.format(channel_id=quote(channel_id, safe="")),
            )
            log_upload_section(
                "YouTube", profile_id, video_id, "Kết thúc", status="Thất bại", level="error"
            )
            return False

        page = None
        response_trace: dict = {}
        try:
            progress(3, "Đang kết nối Chromium %s.", browser_label)
            with connected_gemlogin_profile(
                gemlogin_profile_id,
                config.API_URL,
                profile_config=profile,
            ) as browser, ExitStack() as page_cleanup:
                progress(3, "Đã kết nối Chromium thành công.")
                if not browser.contexts:
                    raise RuntimeError("Không tìm thấy phiên trình duyệt sau khi kết nối CDP.")

                context = browser.contexts[0]
                progress(4, "Đang tạo tab nền và mở YouTube Studio.")
                page = create_background_page(browser, context)
                page_cleanup.callback(_close_page_quietly, page)
                response_trace = attach_response_trace(page)
                upload_url = UPLOAD_URL.format(channel_id=quote(channel_id, safe=""))
                page.goto(upload_url, wait_until="domcontentloaded", timeout=60000)
                progress(4, "YouTube Studio đã tải xong.")

                select_button = page.locator(
                    'button[aria-label="Chọn tệp"][aria-disabled="false"], '
                    'button[aria-label="Select files"][aria-disabled="false"]'
                ).first

                def trigger_youtube_file_chooser():
                    select_button.wait_for(state="visible", timeout=60000)
                    select_button.click(force=True)

                progress(4, "Đang đưa tệp Shorts đã chuyển đổi vào YouTube Studio.")
                set_video_file_background(
                    page,
                    str(converted_path),
                    trigger_youtube_file_chooser,
                )
                progress(4, "YouTube Studio đã nhận tệp; chờ biểu mẫu chi tiết.")

                time.sleep(5)
                title, hashtags = _build_youtube_text(str(converted_path), video, profile)

                title_box = page.locator(
                    'div#textbox[contenteditable="true"][aria-required="true"]'
                ).first
                title_box.wait_for(state="visible", timeout=60000)
                title_box.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.keyboard.insert_text(title)

                description_box = page.locator(
                    'div#textbox[contenteditable="true"][aria-required="false"]'
                ).first
                description_box.wait_for(state="visible", timeout=30000)
                description_box.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                if hashtags:
                    page.keyboard.insert_text(hashtags)
                use_original = bool(
                    profile.get("_runtime_use_original_desc", False)
                    or youtube_cfg.get("use_original_desc", False)
                )
                progress(
                    5,
                    "Đã nhập tiêu đề (%s ký tự) và mô tả hashtag (%s ký tự), nguồn=%s.",
                    len(title),
                    len(hashtags),
                    "mô tả gốc" if use_original else "mô tả cấu hình",
                )

                progress(6, "Đang chờ YouTube tải lên và kiểm tra nội dung/bản quyền.")
                check_result = _wait_for_youtube_checks_complete(
                    page,
                    profile_id,
                    video_id,
                )
                if check_result == YOUTUBE_CHECK_COPYRIGHT:
                    raise UploadSkipped(
                        "YouTube phát hiện nội dung được xác nhận quyền sở hữu."
                    )
                if check_result != YOUTUBE_CHECK_COMPLETE:
                    raise RuntimeError("YouTube không hoàn tất kiểm tra nội dung trong thời gian chờ.")

                next_button = page.locator(
                    'button[aria-label="Tiếp"][aria-disabled="false"], '
                    'button[aria-label="Next"][aria-disabled="false"]'
                ).first
                for step in range(3):
                    next_button.wait_for(state="visible", timeout=60000)
                    next_button.click(force=True)
                    progress(6, "Đã nhấn Tiếp %s/3.", step + 1)
                    time.sleep(1)

                _select_public_visibility(page, timeout_seconds=30)
                progress(6, "Đã chọn quyền hiển thị Công khai.")

                progress(7, "Đang chờ và nhấn nút Lưu/Xuất bản.")
                save_button = page.locator(
                    '#done-button button[aria-label="Lưu"][aria-disabled="false"], '
                    '#done-button button[aria-label="Save"][aria-disabled="false"], '
                    'ytcp-button#done-button[aria-disabled="false"] button'
                ).first
                save_button.wait_for(state="visible", timeout=30000)
                save_button.click(force=True)
                progress(7, "Đã nhấn Lưu/Xuất bản; đang chờ YouTube xác nhận.")
                publish_confirmation = _wait_for_youtube_publish_confirmation(page)
                if publish_confirmation:
                    progress(7, "YouTube đã xác nhận xuất bản (%s).", publish_confirmation)
                else:
                    progress(
                        7,
                        "Không đọc được thông báo xác nhận sau 45 giây; lệnh xuất bản đã được gửi.",
                        level="warning",
                    )
                upload_succeeded = True

        except UploadSkipped as exc:
            logger.warning(
                "[Đăng YouTube][Profile %s][Video %s] BỎ QUA sau %.1f giây | lý do=%s",
                profile_id,
                video_id,
                time.monotonic() - started_at,
                exc,
            )
            log_upload_section(
                "YouTube", profile_id, video_id, "Kết thúc", status="Bỏ qua", level="warning"
            )
            raise
        except Exception as exc:
            logger.exception(
                "[Đăng YouTube][Profile %s][Video %s] THẤT BẠI sau %.1f giây | lỗi=%s",
                profile_id,
                video_id,
                time.monotonic() - started_at,
                exc,
            )
            record_browser_diagnostic(
                page=page,
                profile_id=profile_id,
                video_id=str(video.aweme_id),
                platform="youtube",
                error=exc,
                url=UPLOAD_URL.format(channel_id=quote(channel_id, safe="")),
                last_response=response_trace,
            )
        finally:
            if page is not None:
                _close_page_quietly(page)
            if converted_path is not None:
                try:
                    Path(converted_path).unlink(missing_ok=True)
                    logger.info("[Profile %s] Đã xóa video 9:16 tạm: %s", profile_id, converted_path)
                except OSError as exc:
                    logger.warning("Không thể xóa video 9:16 tạm %s: %s", converted_path, exc)

        if upload_succeeded and publish_confirmation:
            logger.info(
                "[Đăng YouTube][Profile %s][Video %s] HOÀN TẤT THÀNH CÔNG sau %.1f giây | xác nhận=%s",
                profile_id,
                video_id,
                time.monotonic() - started_at,
                publish_confirmation,
            )
        elif upload_succeeded:
            logger.warning(
                "[Đăng YouTube][Profile %s][Video %s] HOÀN TẤT GỬI XUẤT BẢN sau %.1f giây | chưa đọc được xác nhận hiển thị từ YouTube.",
                profile_id,
                video_id,
                time.monotonic() - started_at,
            )
        log_upload_section(
            "YouTube",
            profile_id,
            video_id,
            "Kết thúc",
            status=(
                "Thành công"
                if upload_succeeded and publish_confirmation
                else "Đã gửi xuất bản"
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
        return upload_succeeded
