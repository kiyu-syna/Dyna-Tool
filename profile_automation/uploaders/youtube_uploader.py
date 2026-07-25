import re
import time
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import quote


import core.config as config
from core.utils import console, logger
from profile_automation.browser_utils import set_video_file_background
from profile_automation.uploaders.base_uploader import BaseUploader, UploadSkipped
from profile_automation.watchers.douyin_profile_monitor import DouyinVideo
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


def _wait_for_youtube_checks_complete(page, profile_id: str, timeout_seconds: int = 900) -> str:
    progress_labels = page.locator("span.progress-label.style-scope.ytcp-video-upload-progress")
    deadline = time.monotonic() + timeout_seconds
    last_text = ""
    last_log_at = 0.0

    while time.monotonic() < deadline:
        try:
            labels = progress_labels.all_inner_texts()
        except Exception as exc:
            logger.debug("[Profile %s] Không đọc được trạng thái kiểm tra YouTube: %s", profile_id, exc)
            labels = []

        normalized_labels = [" ".join(label.split()) for label in labels]
        check_status = _classify_youtube_check_status(normalized_labels)
        if check_status == YOUTUBE_CHECK_COPYRIGHT:
            visible_text = " | ".join(label for label in normalized_labels if label)
            logger.warning(
                "[Profile %s] YouTube phát hiện nội dung được xác nhận quyền sở hữu; "
                "hủy đăng video này.",
                profile_id,
            )
            console.print("   YouTube phát hiện nội dung bản quyền. Đã hủy đăng video này.")
            return YOUTUBE_CHECK_COPYRIGHT
        if check_status == YOUTUBE_CHECK_COMPLETE:
            logger.info("[Profile %s] YouTube đã kiểm tra xong, không phát hiện vấn đề.", profile_id)
            console.print("   YouTube checks completed. No issues detected.")
            return YOUTUBE_CHECK_COMPLETE

        visible_text = " | ".join(label for label in normalized_labels if label)
        if visible_text and visible_text != last_text:
            last_text = visible_text
            logger.info("[Profile %s] Trạng thái kiểm tra YouTube: %s", profile_id, visible_text)

        now = time.monotonic()
        if now - last_log_at >= 30:
            elapsed = int(timeout_seconds - max(0, deadline - now))
            console.print(f"      ...Waiting for YouTube checks to finish ({elapsed}s)...")
            last_log_at = now

        time.sleep(2)

    logger.error(
        "[Profile %s] Timed out waiting for YouTube checks to complete. Last status: %s",
        profile_id,
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
        youtube_cfg = profile.get("youtube", {})
        gemlogin_profile_id = str(youtube_cfg.get("gemlogin_profile_id") or profile_id).strip()
        channel_id = str(youtube_cfg.get("channel_id") or "").strip()
        preset = str(youtube_cfg.get("preset") or DEFAULT_PRESET).strip().lower()
        crf = youtube_cfg.get("crf", DEFAULT_CRF)

        if not channel_id:
            logger.error(
                "[Profile %s] YouTube Shorts skipped: Channel ID is not configured.",
                profile_id,
            )
            return False

        converted_path = None
        upload_succeeded = False
        try:
            converted_path = convert_to_youtube_shorts(
                video_path,
                preset=preset,
                crf=crf,
                profile_id=profile_id,
                video_id=str(video.aweme_id),
                priority=int(profile.get("_workload_priority", 100)),
                cancel_event=profile.get("_runtime_cancel_event"),
            )
        except (YouTubeShortsConversionError, OSError, ValueError) as exc:
            logger.error("[Profile %s] Chuyển đổi YouTube Shorts thất bại: %s", profile_id, exc)
            record_browser_diagnostic(
                profile_id=profile_id,
                video_id=str(video.aweme_id),
                platform="youtube",
                error=exc,
                url=UPLOAD_URL.format(channel_id=quote(channel_id, safe="")),
            )
            return False

        logger.info("==========================================================")
        logger.info(
            "BẮT ĐẦU ĐĂNG YOUTUBE SHORTS - PROFILE %s (%s)",
            profile_id,
            browser_profile_label(gemlogin_profile_id, profile),
        )
        logger.info("==========================================================")

        page = None
        response_trace: dict = {}
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
                upload_url = UPLOAD_URL.format(channel_id=quote(channel_id, safe=""))
                logger.info("[Profile %s] Đang mở trang đăng YouTube: %s", profile_id, upload_url)
                page.goto(upload_url, wait_until="domcontentloaded", timeout=60000)

                select_button = page.locator(
                    'button[aria-label="Chọn tệp"][aria-disabled="false"], '
                    'button[aria-label="Select files"][aria-disabled="false"]'
                ).first

                def trigger_youtube_file_chooser():
                    select_button.wait_for(state="visible", timeout=60000)
                    select_button.click(force=True)

                set_video_file_background(page, str(converted_path), trigger_youtube_file_chooser)
                logger.info("[Profile %s] Đã chọn video YouTube bằng Playwright ở chế độ nền.", profile_id)

                # Step 5: allow YouTube to accept the selected file.
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
                logger.info(
                    "[Profile %s] Đã nhập tiêu đề và phần mô tả hashtag.",
                    profile_id,
                )

                console.print("   Waiting for YouTube checks to complete before continuing...")
                check_result = _wait_for_youtube_checks_complete(page, profile_id)
                if check_result == YOUTUBE_CHECK_COPYRIGHT:
                    raise UploadSkipped(
                        "YouTube phát hiện nội dung được xác nhận quyền sở hữu."
                    )
                if check_result != YOUTUBE_CHECK_COMPLETE:
                    return False

                next_button = page.locator(
                    'button[aria-label="Tiếp"][aria-disabled="false"], '
                    'button[aria-label="Next"][aria-disabled="false"]'
                ).first
                for step in range(3):
                    next_button.wait_for(state="visible", timeout=60000)
                    next_button.click(force=True)
                    logger.info("[Profile %s] Đã nhấn Tiếp, bước %s/3.", profile_id, step + 1)
                    time.sleep(1)

                _select_public_visibility(page, timeout_seconds=30)
                logger.info(
                    "[Profile %s] Đã chọn chế độ công khai trên YouTube.",
                    profile_id,
                )

                save_button = page.locator(
                    '#done-button button[aria-label="Lưu"][aria-disabled="false"], '
                    '#done-button button[aria-label="Save"][aria-disabled="false"], '
                    'ytcp-button#done-button[aria-disabled="false"] button'
                ).first
                save_button.wait_for(state="visible", timeout=30000)
                save_button.click(force=True)
                time.sleep(5)
                upload_succeeded = True
                logger.info("[Profile %s] Đăng YouTube Shorts hoàn tất.", profile_id)
                page.close()

        except UploadSkipped:
            raise
        except Exception as exc:
            logger.exception("[Profile %s] Đăng YouTube Shorts thất bại: %s", profile_id, exc)
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

        return upload_succeeded
