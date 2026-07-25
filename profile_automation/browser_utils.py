import os
import time

from core.utils import logger

PLAYWRIGHT_REMOTE_FILE_LIMIT_BYTES = 50 * 1024 * 1024
CDP_FILE_CHOOSER_TIMEOUT_MS = 10000


def _attributes_to_dict(attributes):
    return {
        str(attributes[index]): str(attributes[index + 1])
        for index in range(0, len(attributes) - 1, 2)
    }


def _accepts_video_file(attributes):
    accept = str(attributes.get("accept") or "").lower()
    return not accept or any(token in accept for token in ("video", "mp4", "*/*"))


def _set_video_file_via_cdp(page, absolute_path, trigger_file_chooser=None, timeout_ms=60000):
    """Let the local Chromium process read the file path, avoiding Playwright's 50 MB transfer."""
    session = page.context.new_cdp_session(page)
    chooser_event = {}
    intercept_enabled = False

    def on_file_chooser_opened(params):
        chooser_event.clear()
        chooser_event.update(params or {})

    try:
        if trigger_file_chooser is not None:
            session.on("Page.fileChooserOpened", on_file_chooser_opened)
            session.send("Page.setInterceptFileChooserDialog", {"enabled": True})
            intercept_enabled = True
            trigger_file_chooser()

            deadline = time.monotonic() + min(
                max(1, timeout_ms),
                CDP_FILE_CHOOSER_TIMEOUT_MS,
            ) / 1000
            while not chooser_event.get("backendNodeId") and time.monotonic() < deadline:
                page.wait_for_timeout(100)

            backend_node_id = chooser_event.get("backendNodeId")
            if backend_node_id:
                session.send(
                    "DOM.setFileInputFiles",
                    {
                        "files": [absolute_path],
                        "backendNodeId": backend_node_id,
                    },
                )
                return True

        document = session.send("DOM.getDocument", {"depth": 1, "pierce": True})
        root_node_id = (document.get("root") or {}).get("nodeId")
        if not root_node_id:
            return False
        query_result = session.send(
            "DOM.querySelectorAll",
            {"nodeId": root_node_id, "selector": 'input[type="file"]'},
        )
        for node_id in query_result.get("nodeIds") or []:
            attributes_result = session.send("DOM.getAttributes", {"nodeId": node_id})
            attributes = _attributes_to_dict(attributes_result.get("attributes") or [])
            if not _accepts_video_file(attributes):
                continue
            session.send(
                "DOM.setFileInputFiles",
                {"files": [absolute_path], "nodeId": node_id},
            )
            return True
        return False
    finally:
        if intercept_enabled:
            try:
                session.send("Page.setInterceptFileChooserDialog", {"enabled": False})
            except Exception:
                pass
        try:
            session.detach()
        except Exception:
            pass


def set_video_file_background(page, video_path, trigger_file_chooser=None, timeout_ms=60000):
    """Attach a video through Playwright without activating the browser or OS dialog."""
    absolute_path = os.path.abspath(video_path)
    if not os.path.isfile(absolute_path):
        raise FileNotFoundError(absolute_path)

    cdp_error = None
    file_size = os.path.getsize(absolute_path)

    # GemLogin is connected through a remote Playwright/CDP websocket. Using
    # FileChooser.set_files there uploads the whole file over that websocket and
    # can hit Playwright's default 30-second timeout even for a moderately sized
    # converted Short. Prefer DOM.setFileInputFiles whenever we can open the
    # chooser ourselves; Chromium then reads the local path directly.
    if trigger_file_chooser is not None or file_size > PLAYWRIGHT_REMOTE_FILE_LIMIT_BYTES:
        try:
            if _set_video_file_via_cdp(
                page,
                absolute_path,
                trigger_file_chooser=trigger_file_chooser,
                timeout_ms=timeout_ms,
            ):
                logger.info(
                    "Da gan video bang duong dan cuc bo qua CDP: %s",
                    absolute_path,
                )
                return
        except Exception as exc:
            cdp_error = exc
            logger.debug("Gan video qua CDP that bai, thu Playwright: %s", exc)

    chooser_error = None
    if trigger_file_chooser is not None:
        try:
            with page.expect_file_chooser(timeout=timeout_ms) as chooser_info:
                trigger_file_chooser()
            chooser_info.value.set_files(absolute_path, timeout=timeout_ms)
            return
        except Exception as exc:
            chooser_error = exc
            logger.debug("Khong bat duoc file chooser, thu gan truc tiep input file: %s", exc)

    file_inputs = page.locator('input[type="file"]')
    for index in range(file_inputs.count()):
        file_input = file_inputs.nth(index)
        try:
            accept = (file_input.get_attribute("accept") or "").lower()
            if accept and not any(token in accept for token in ("video", "mp4", "*/*")):
                continue
            file_input.set_input_files(absolute_path)
            return
        except Exception as exc:
            logger.debug("Khong the gan video vao input file thu %s: %s", index, exc)

    try:
        if _set_video_file_via_cdp(page, absolute_path, timeout_ms=timeout_ms):
            logger.info("Da gan video bang input file cuc bo qua CDP: %s", absolute_path)
            return
    except Exception as exc:
        cdp_error = exc
        logger.debug("Gan video qua input file CDP that bai: %s", exc)

    if cdp_error is not None:
        raise RuntimeError(
            f"Khong the chon video bang Playwright hoac CDP. Chi tiet CDP: {cdp_error}"
        ) from cdp_error
    if chooser_error is not None:
        raise RuntimeError("Khong the chon video bang file chooser hoac input file.") from chooser_error
    raise RuntimeError("Khong tim thay input chon video tren trang.")

def is_captcha_present(page):
    """
    Kiểm tra sự hiện diện của Captcha trên trang (Douyin/TikTok).
    Cứng hóa: yêu cầu phần tử phải visible VÀ có kích thước đủ lớn (>50px)
    để tránh false positive từ các phần tử ẩn hoặc nhỏ.
    """
    try:
        # Bước 1: Kiểm tra bằng selector đặc hiệu (ưu tiên cao, ít false positive)
        specific_selectors = [
            '#captcha_container',
            '.captcha_verify_container',
            '.tiktok-captcha-container',
        ]
        for sel in specific_selectors:
            try:
                loc = page.locator(sel)
                if loc.is_visible(timeout=500):
                    # Kiểm tra thêm kích thước để chắc chắn đây là captcha thật
                    box = loc.bounding_box()
                    if box and box['width'] > 50 and box['height'] > 50:
                        logger.info(f"is_captcha_present: Phát hiện captcha qua selector '{sel}' (size: {box['width']:.0f}x{box['height']:.0f})")
                        return True
            except Exception:
                continue

        # Bước 2: Kiểm tra bằng selector mở rộng (wildcard) nhưng cần thêm điều kiện size
        wildcard_selectors = [
            '[id*="captcha"]',
            '[class*="captcha"]',
        ]
        for sel in wildcard_selectors:
            try:
                loc = page.locator(sel)
                if loc.is_visible(timeout=500):
                    box = loc.bounding_box()
                    # Yêu cầu phần tử phải đủ lớn (>100px cả chiều rộng lẫn chiều cao)
                    # để loại bỏ các phần tử captcha ẩn hoặc badge nhỏ
                    if box and box['width'] > 100 and box['height'] > 100:
                        logger.info(f"is_captcha_present: Phát hiện captcha qua wildcard '{sel}' (size: {box['width']:.0f}x{box['height']:.0f})")
                        return True
            except Exception:
                continue

        # Bước 3: Kiểm tra text chỉ với các từ khóa rất đặc hiệu cho captcha
        # (Loại bỏ "verify", "robot" vì quá chung, dễ false positive)
        captcha_text_patterns = [
            "请完成验证",        # Douyin: "Vui lòng hoàn thành xác minh"
            "滑动滑块",          # Douyin: "Trượt thanh trượt"
            "点击按住",          # Douyin: "Nhấn giữ"
            "拼图验证",          # Douyin: "Xác minh ghép hình"
            "Verify to continue",  # TikTok EN
            "Drag the slider",     # TikTok EN  
            "Xác minh để tiếp tục", # TikTok VN
        ]
        for text_pattern in captcha_text_patterns:
            try:
                loc = page.get_by_text(text_pattern, exact=False)
                if loc.is_visible(timeout=500):
                    logger.info(f"is_captcha_present: Phát hiện captcha qua text '{text_pattern}'")
                    return True
            except Exception:
                continue
                
        return False
    except Exception as e:
        logger.debug(f"is_captcha_present: Lỗi tổng quát khi kiểm tra captcha: {e}")
        return False

