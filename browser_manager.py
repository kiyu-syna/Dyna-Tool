from utils import extract_id, logger
from config import TARGET_TAGS

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
