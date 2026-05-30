"""
auto_downloader.py
──────────────────
Engine tự động tải video hàng loạt qua SO9 Downloader.
Port từ Chrome Extension KINGAUTOMATIONAI AUTODOWLOAD → Python/Playwright.

Hỗ trợ: Facebook, TikTok, Instagram, Douyin
"""

import asyncio
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

# ── Routing: platform → URL downloader SO9 ────────────────────────────────────
ROUTES = [
    {"platform": "facebook",  "patterns": ["facebook.com", "fb.watch"],        "url": "https://so9.vn/9downloader/facebook"},
    {"platform": "tiktok",    "patterns": ["tiktok.com", "vm.tiktok.com"],      "url": "https://so9.vn/9downloader/tiktok"},
    {"platform": "instagram", "patterns": ["instagram.com"],                    "url": "https://so9.vn/9downloader/insta"},
    {"platform": "douyin",    "patterns": ["douyin.com"],                       "url": "https://so9.vn/9downloader/douyin"},
]

# ── Trạng thái một item trong hàng đợi ───────────────────────────────────────
STATUS_PENDING     = "pending"
STATUS_RUNNING     = "running"
STATUS_SUCCESS     = "success"
STATUS_FAILED      = "failed"
STATUS_UNSUPPORTED = "unsupported"

STATUS_LABELS = {
    STATUS_PENDING:     "Chờ tải",
    STATUS_RUNNING:     "Đang tải",
    STATUS_SUCCESS:     "Thành công",
    STATUS_FAILED:      "Thất bại",
    STATUS_UNSUPPORTED: "Không hỗ trợ",
}


@dataclass
class DownloadItem:
    id: str
    link: str
    platform: str
    downloader_url: str
    status: str = STATUS_PENDING
    message: str = "Chờ xử lý"
    metadata: dict = field(default_factory=dict)


# ── Helper: phân loại URL ──────────────────────────────────────────────────────
def detect_route(link: str) -> Optional[dict]:
    lower = link.lower()
    for route in ROUTES:
        if any(p in lower for p in route["patterns"]):
            return route
    return None


def extract_links(text: str) -> List[str]:
    matches = re.findall(r'https?://[^\s"\'<>]+', text)
    seen = set()
    result = []
    for m in matches:
        clean = re.sub(r'[),.;]+$', '', m)
        if clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def build_queue(text: str) -> List[DownloadItem]:
    links = extract_links(text)
    items = []
    for i, link in enumerate(links):
        route = detect_route(link)
        item = DownloadItem(
            id=f"{int(time.time()*1000)}-{i}",
            link=link,
            platform=route["platform"] if route else "unknown",
            downloader_url=route["url"] if route else "",
            status=STATUS_PENDING if route else STATUS_UNSUPPORTED,
            message="Chờ xử lý" if route else "Không hỗ trợ domain này",
        )
        items.append(item)
    return items


# ── Playwright automation ─────────────────────────────────────────────────────
async def _type_like_human(page: Page, selector: str, value: str):
    """Điền text an toàn cho các form React/Vue bằng Playwright fill."""
    try:
        await page.fill(selector, "")
        await asyncio.sleep(0.1)
        await page.fill(selector, value)
    except Exception as e:
        # Fallback
        await page.evaluate(f"document.querySelector('{selector}').value = '{value}'")


async def _find_input(page: Page) -> Optional[str]:
    """Tìm ô nhập link trên trang SO9 downloader."""
    selectors = [
        ".download-input-field input[type='text']",
        ".download-input-field textarea",
        "input[placeholder*='link' i]",
        "input[placeholder*='url' i]",
        "textarea[placeholder*='link' i]",
        "input[type='text']:not([type='hidden'])",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=1000):
                return sel
        except Exception:
            continue
    return None


async def _find_download_button(page: Page) -> Optional[str]:
    """Tìm nút Tải xuống trên trang SO9."""
    candidates = [
        ".download-input-field .origin-button",
        ".download-input-field button",
        "form button[type='submit']",
        "form input[type='submit']",
        "button:has-text('Tải')",
        "button:has-text('Download')",
        "button:has-text('Get link')",
        "[role='button']:has-text('Tải')",
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=800):
                return sel
        except Exception:
            continue
    return None


async def _wait_for_download_link(page: Page, timeout_ms: int = 45000) -> Optional[str]:
    """Chờ link download xuất hiện trên trang sau khi submit."""
    media_pattern = re.compile(
        r'\.(mp4|mov|webm|m4v|jpg|jpeg|png|webp)(\?|$)',
        re.IGNORECASE
    )
    cdn_pattern = re.compile(r'/(video|videos|media|downloader)/', re.IGNORECASE)

    deadline = time.time() + timeout_ms / 1000

    while time.time() < deadline:
        # Kiểm tra lỗi trang
        try:
            body_text = await page.evaluate("""() => {
                const text = (document.body?.innerText || '').toLowerCase();
                return text;
            }""")
            if any(p in body_text for p in ["link không hợp lệ", "khong hop le", "invalid link", "not valid"]):
                return None
        except Exception:
            pass

        # Tìm link media trực tiếp
        try:
            hrefs = await page.evaluate("""() => {
                return [...document.querySelectorAll('a[href]')]
                    .map(a => a.href)
                    .filter(h => h && (h.startsWith('blob:') || h.startsWith('http')));
            }""")
            for href in hrefs:
                parsed_path = href.split('?')[0].lower()
                if media_pattern.search(parsed_path):
                    return href
                if 'cdn' in href.lower() and cdn_pattern.search(href):
                    return href
        except Exception:
            pass

        await asyncio.sleep(0.7)

    return None


async def _detect_page_error(page: Page) -> str:
    """Kiểm tra lỗi trên trang SO9."""
    try:
        text = await page.evaluate("""() =>
            (document.body?.innerText || '').toLowerCase()
        """)
        invalid_patterns = [
            "link không hợp lệ", "khong hop le", "hãy thử lại",
            "hay thu lai", "invalid link", "not valid"
        ]
        if any(p in text for p in invalid_patterns):
            return "SO9 báo link không hợp lệ hoặc không thể tải link này."
    except Exception:
        pass
    return ""


# ── Engine chính: chạy hàng đợi ──────────────────────────────────────────────
class AutoDownloaderEngine:
    """
    Engine bất đồng bộ chạy trong thread riêng.
    Giao tiếp với GUI qua callback.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.running = False
        self.paused = False
        self.stopped = False

    def start(
        self,
        queue: List[DownloadItem],
        download_folder: str,
        on_item_update: Callable[[DownloadItem], None],
        on_log: Callable[[str, str], None],
        on_finish: Callable[[], None],
    ):
        """Khởi động engine trong thread nền."""
        if self.running:
            return
        self.running = True
        self.paused = False
        self.stopped = False
        self._thread = threading.Thread(
            target=self._run_thread,
            args=(queue, download_folder, on_item_update, on_log, on_finish),
            daemon=True,
        )
        self._thread.start()

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        self.stopped = True
        self.paused = False
        self.running = False

    def _run_thread(self, queue, download_folder, on_item_update, on_log, on_finish):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(
                self._run_queue(queue, download_folder, on_item_update, on_log)
            )
        finally:
            self._loop.close()
            self.running = False
            on_finish()

    async def _run_queue(self, queue, download_folder, on_item_update, on_log):
        os.makedirs(download_folder, exist_ok=True)
        on_log("Bắt đầu xử lý danh sách link.", "info")

        async with async_playwright() as pw:
            # Ưu tiên dùng trình duyệt đã cài trong máy (Chrome → Edge → Chromium built-in)
            browser: Browser = None
            launch_args = ["--disable-blink-features=AutomationControlled"]
            for channel in ("chrome", "msedge"):
                try:
                    browser = await pw.chromium.launch(
                        headless=False,
                        channel=channel,
                        args=launch_args,
                    )
                    on_log(f"Đang dùng trình duyệt: {channel.title()}", "info")
                    break
                except Exception:
                    pass

            if browser is None:
                # Fallback: Playwright Chromium built-in
                on_log("Không tìm thấy Chrome/Edge, dùng Chromium built-in.", "warn")
                browser = await pw.chromium.launch(
                    headless=False,
                    args=launch_args,
                )

            context: BrowserContext = await browser.new_context(
                accept_downloads=True,
            )
            # Đặt thư mục download
            await context.grant_permissions(["notifications"])


            try:
                page = await context.new_page()
                current_url = ""
                for item in queue:
                    if self.stopped:
                        break

                    # Chờ nếu đang tạm dừng
                    while self.paused and not self.stopped:
                        await asyncio.sleep(0.6)

                    if self.stopped:
                        break

                    if item.status in (STATUS_UNSUPPORTED, STATUS_SUCCESS):
                        continue

                    # Cập nhật trạng thái: đang chạy
                    item.status = STATUS_RUNNING
                    item.message = "Đang xử lý downloader"
                    on_item_update(item)
                    on_log(f"Đang xử lý [{item.platform.upper()}]: {item.link}", "info")

                    try:
                        result = await self._process_item(page, item, current_url, download_folder, on_log)
                        current_url = item.downloader_url
                        item.status = STATUS_SUCCESS
                        item.message = f"Đã tải: {result}" if result else "Đã hoàn tất"
                        on_log(f"✅ Thành công: {item.link}", "info")
                    except Exception as e:
                        item.status = STATUS_FAILED
                        item.message = str(e)[:120]
                        on_log(f"❌ Thất bại: {item.link} — {e}", "error")

                    on_item_update(item)

            finally:
                try:
                    await context.close()
                    await browser.close()
                except Exception:
                    pass

        msg = "Tiến trình đã dừng." if self.stopped else "Đã xử lý xong danh sách link."
        on_log(msg, "warn" if self.stopped else "info")

    async def _process_item(
        self,
        page: Page,
        item: DownloadItem,
        current_url: str,
        download_folder: str,
        on_log: Callable,
        timeout_ms: int = 90_000,
    ) -> str:
        """Xử lý một link: mở trang SO9 → điền link → click tải → chờ download."""
        if current_url != item.downloader_url:
            await page.goto(item.downloader_url, timeout=timeout_ms, wait_until="domcontentloaded")
            on_log(f"  → Trang SO9 đã tải: {item.downloader_url}", "info")
        else:
            on_log(f"  → Tái sử dụng tab hiện tại", "info")

        # Tìm ô input
        input_sel = None
        deadline = time.time() + 25
        while time.time() < deadline:
            input_sel = await _find_input(page)
            if input_sel:
                break
            await asyncio.sleep(0.5)

        if not input_sel:
            raise RuntimeError("Không tìm thấy ô nhập link trên trang SO9.")

        # Điền link chậm rãi
        await _type_like_human(page, input_sel, item.link)
        on_log(f"  → Đã nhập link vào ô input", "info")
        await asyncio.sleep(1) # Chờ 1 chút như người dùng

        # Tìm nút Tải
        btn_sel = await _find_download_button(page)
        if not btn_sel:
            raise RuntimeError("Không tìm thấy nút tải xuống trên trang SO9.")

        # Thay vì đi tìm nút Tải (có thể tìm nhầm nút cũ), ta dùng phím Enter cho chuẩn xác
        old_url = page.url
        try:
            await page.click(btn_sel)
            # Chờ cho đến khi URL của web thay đổi (nghĩa là đã nhận link mới)
            timeout = time.time() + 30
            while page.url == old_url and time.time() < timeout:
                await asyncio.sleep(0.5)
                
            on_log(f"  → Đã nhận dạng URL thay đổi, đang chờ icon kết quả...", "info")
            await asyncio.sleep(1) # Chờ xíu cho icon render
        except Exception as e:
            on_log(f"  → Lỗi khi bấm nút tải: {e}", "warn")

        # Chờ icon download thực sự xuất hiện
        download_icon_sel = "i.bx.bxs-cloud-download"
        try:
            # SO9 có thể mất thời gian xử lý video nên chờ tới 45s cho kết quả mới
            await page.wait_for_selector(download_icon_sel, timeout=45000)
            on_log(f"  → Đã tìm thấy nút tải kết quả, tiến hành click tải về...", "info")
            
            # Quay lại dùng thao tác click download gốc vì thao tác này hoạt động thành công
            # User chỉ bị lỗi tên file (UUID) và không có đuôi .mp4
            async with page.expect_download(timeout=timeout_ms) as dl_info:
                await page.click(download_icon_sel)
                
            download = await dl_info.value
            
            # Khắc phục hoàn toàn lỗi tên file UUID không có đuôi của SO9:
            # Ép luôn tên file dễ đọc, có time để không bị trùng, và CHẮC CHẮN CÓ ĐUÔI .mp4
            filename = f"video_{int(time.time())}.mp4"
            
            os.makedirs(download_folder, exist_ok=True)
            save_path = os.path.join(download_folder, filename)
            
            # Playwright sẽ copy file đã tải (dù có là UUID đi nữa) sang đúng save_path với tên mp4 mới
            await download.save_as(save_path)

            on_log(f"  → File đã lưu thành công tại:\n    {save_path}", "info")
            return filename

            
        except Exception as e:
            # Nếu hết 45s không thấy thẻ i tải xuống, kiểm tra xem có thông báo lỗi từ SO9 không
            err = await _detect_page_error(page)
            raise RuntimeError(err or f"Lỗi tải file (quá hạn hoặc không thấy icon): {e}")


# ── Singleton engine (dùng chung trong toàn bộ app) ──────────────────────────
_engine_instance: Optional[AutoDownloaderEngine] = None


def get_engine() -> AutoDownloaderEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = AutoDownloaderEngine()
    return _engine_instance


def normalize_folder(value: str) -> str:
    """Chuẩn hóa tên thư mục download."""
    if not value or not value.strip():
        return "Dyna-Downloads"
    # Giữ nguyên đường dẫn tuyệt đối của Windows, chỉ normpath
    return os.path.normpath(value.strip())
