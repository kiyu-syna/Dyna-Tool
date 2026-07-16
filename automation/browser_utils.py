import os
import time

from core.utils import extract_id, logger
from core.config import TARGET_TAGS

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

def extract_id_from_page(page):
    """Lấy video ID từ URL hoặc DOM khi Douyin mở video dạng overlay"""
    vid = extract_id(page.url)
    if vid:
        return vid
    try:
        href = page.evaluate("window.location.href")
        vid = extract_id(href)
        if vid:
            return vid
    except Exception as e:
        logger.debug(f"extract_id_from_page: Lỗi khi evaluate href: {e}")
    try:
        # Trong trang đề xuất, quét tìm aweme_id trong bất kỳ link nào (ví dụ thẻ hashtag)
        # của phần tử đang visible
        href = page.evaluate("""
            (() => {
                // Thử tìm trong các link thẻ a trước
                const links = document.querySelectorAll('a[href*="/video/"], a[href*="aweme_id="]');
                for (const el of links) {
                    const rect = el.getBoundingClientRect();
                    if (rect.top >= 0 && rect.bottom <= (window.innerHeight || document.documentElement.clientHeight)) {
                        return el.href || el.getAttribute('href') || '';
                    }
                }
                
                // Nếu không có, gom hết tất cả các thẻ a trên trang
                const allLinks = document.querySelectorAll('a');
                for (const el of allLinks) {
                    const href = el.href || el.getAttribute('href') || '';
                    if (href.includes('/video/') || href.includes('aweme_id=')) {
                        return href;
                    }
                }
                
                return '';
            })()
        """)
        return extract_id(href)
    except Exception as e:
        logger.debug(f"extract_id_from_page: Lỗi khi quét DOM tìm video link: {e}")
        return None

def get_active_video_data(page):
    """
    Lấy thông tin (mô tả, tim, thời lượng) của video đang active trên màn hình.
    Sử dụng bounding box để đảm bảo không lấy nhầm video phía trên/dưới.
    """
    try:
        return page.evaluate("""
            (() => {
                // 1. Tìm container của video đang hoạt động
                let container = document.querySelector('[data-e2e="feed-active-video"]');
                if (!container) {
                    const containers = document.querySelectorAll('.video-card-container, .swiper-slide-active, [data-e2e="feed-video"]');
                    for (const c of containers) {
                        const rect = c.getBoundingClientRect();
                        // Video đang active thường nằm sát đỉnh (top ~ 0)
                        if (rect.top >= -150 && rect.top <= 150) {
                            container = c;
                            break;
                        }
                    }
                }
                
                // Nếu vẫn không tìm thấy container đặc hiệu, dùng body làm gốc nhưng sẽ check rect từng phần tử
                const root = container || document.body;

                const findText = (selectors) => {
                    for (const sel of selectors) {
                        // Nếu có container, tìm bên trong container trước
                        if (container) {
                            const el = container.querySelector(sel);
                            if (el && el.innerText && el.innerText.trim()) return el.innerText.trim();
                        }
                        
                        // Nếu không thấy hoặc không có container, quét toàn trang nhưng lọc theo tọa độ
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            const rect = el.getBoundingClientRect();
                            // Chỉ lấy phần tử nằm trong vùng nhìn thấy (viewport)
                            if (rect.top >= 0 && rect.bottom <= (window.innerHeight || document.documentElement.clientHeight)) {
                                if (el.innerText && el.innerText.trim()) return el.innerText.trim();
                            }
                        }
                    }
                    return "";
                };

                return {
                    description: findText(['[data-e2e="video-desc"]', '[class*="title"][class*="cursor"]', '[class*="video-desc"]']),
                    likes: findText(['div[class*="aKy92uTH"]', 'div[class*="KV_gO8oI"]', 'div[class*="uwkzJlBF"]']),
                    duration: findText(['span.time-duration', '.xgplayer-time-duration', 'span[class*="duration"]', 'div[class*="timeDuration"]'])
                };
            })()
        """)
    except Exception as e:
        logger.warning(f"get_active_video_data: Lỗi khi lấy thông tin video: {e}")
        return {"description": "", "likes": "0", "duration": "00:00"}

def video_has_target_tag(description, target_tags=None):
    """Kiểm tra mô tả có chứa hashtag mục tiêu không (không phân biệt hoa thường)"""
    if not description: return None
    if target_tags is None:
        target_tags = TARGET_TAGS
    desc_lower = description.lower()
    for tag in target_tags:
        if tag.lower() in desc_lower:
            return tag
    return None

def is_live_video(page):
    """Kiểm tra xem video hiện tại có phải là luồng Livestream không"""
    try:
        return page.evaluate("""
            (() => {
                // 1. Thử tìm bằng các class chính xác của thông báo Live giữa màn hình
                const liveEls = document.querySelectorAll('.O5Z8MxbR, .IacIc3kX, .IlvK_NXJ');
                for (const el of liveEls) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.top >= 0 && rect.bottom <= window.innerHeight) {
                        return true; // Thấy class này đang hiện trên màn hình -> Là Live
                    }
                }
                
                // 2. Dự phòng: Tìm bằng nội dung chữ "直播间进入" (Bấm vào phòng live)
                const allDivs = document.querySelectorAll('div');
                for (const el of allDivs) {
                    if (el.innerText && el.innerText.includes('直播间进入') && el.innerText.length < 50) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.top >= 0 && rect.bottom <= window.innerHeight) {
                            return true;
                        }
                    }
                }
                return false;
            })()
        """)
    except Exception as e:
        logger.debug(f"is_live_video: Lỗi khi kiểm tra Live: {e}")
        return False

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


def get_profile_sec_uid(page, profile_url):
    """
    Trích xuất sec_uid từ URL profile Douyin.
    Nếu URL là dạng rút gọn hoặc cần redirect, sẽ mở trang bằng Playwright và bắt từ URL thực tế hoặc request.
    """
    import re
    # Thử parse trực tiếp từ profile_url trước
    match = re.search(r"douyin\.com/user/(MS4wLjAB[a-zA-Z0-9_\-]+)", profile_url)
    if match:
        return match.group(1)
        
    # Nếu không parse được (ví dụ url rút gọn v.douyin.com...), ta mở trang
    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
        # Check url hiện tại của page
        current_url = page.url
        match = re.search(r"douyin\.com/user/(MS4wLjAB[a-zA-Z0-9_\-]+)", current_url)
        if match:
            return match.group(1)
            
        # Thử tìm trong DOM hoặc script tag
        content = page.content()
        match = re.search(r'"secUid"\s*:\s*"(MS4wLjAB[a-zA-Z0-9_\-]+)"', content)
        if match:
            return match.group(1)
            
        match = re.search(r'sec_user_id=(MS4wLjAB[a-zA-Z0-9_\-]+)', content)
        if match:
            return match.group(1)
    except Exception as e:
        logger.error(f"get_profile_sec_uid: Lỗi khi lấy sec_uid từ {profile_url}: {e}")
        
    return None

