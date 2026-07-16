import re
import time
from contextlib import ExitStack
from datetime import datetime

from profile_automation.uploaders.base_uploader import BaseUploader
from profile_automation.watchers.douyin_profile_monitor import DouyinVideo
from core.utils import logger, console, notify_intervention
from automation.browser_utils import is_captcha_present, set_video_file_background
import core.config as config
from services.gemlogin_browser_service import (
    connected_gemlogin_profile,
    create_background_page,
)
from services.diagnostic_artifact_service import attach_response_trace, record_browser_diagnostic

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
CLICK_UPLOAD_X, CLICK_UPLOAD_Y = 1117, 835


HASHTAG_PATTERN = re.compile(r"#\S+")
def _close_page_quietly(page) -> None:
    if page is None:
        return
    try:
        if not page.is_closed():
            page.close()
    except Exception:
        pass

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
        tiktok_cfg = profile.get("tiktok", {})
        gemlogin_profile_id = tiktok_cfg.get("gemlogin_profile_id")
        
        if not gemlogin_profile_id:
            logger.error(f"[Profile {profile_id}] TikTok upload skipped: No gemlogin_profile_id configured.")
            return False
            
        logger.info("==========================================================")
        logger.info(f"   BẮT ĐẦU UPLOAD TIKTOK - PROFILE {profile_id} (GemLogin: {gemlogin_profile_id})   ")
        logger.info("==========================================================")
        
        api_url = config.API_URL
        is_safe = False
        page = None
        response_trace: dict = {}
        with connected_gemlogin_profile(
            gemlogin_profile_id,
            api_url,
        ) as browser, ExitStack() as page_cleanup:
            try:
                if not browser.contexts:
                    logger.error(f"[Profile {profile_id}] Không tìm thấy browser contexts sau khi kết nối CDP.")
                    return False
                context = browser.contexts[0]
                
                # Má»Ÿ trang upload hoáº·c láº¥y page hiá»‡n cÃ³
                page = create_background_page(browser, context)
                page_cleanup.callback(_close_page_quietly, page)
                response_trace = attach_response_trace(page)
                
                logger.info(f"[Profile {profile_id}] Äang Ä‘i tá»›i link upload: {UPLOAD_URL}")
                page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
                time.sleep(5)

                # --- KIỂM TRA CAPTCHA ---
                if is_captcha_present(page):
                    console.print("[bold red]🚨 Phát hiện CAPTCHA khi upload! Đang gửi thông báo...[/]")
                    notify_intervention("Phát hiện CAPTCHA trên TikTok Studio. Vui lòng xử lý để tiếp tục upload.", page)
                    while is_captcha_present(page):
                        time.sleep(5)
                    console.print("[bold green]✅ CAPTCHA đã được xử lý. Tiếp tục...[/]")

                # Thiết lập behavior download cho an toàn
                try:
                    client = page.context.new_cdp_session(page)
                    client.send("Browser.setDownloadBehavior", {"behavior": "default"})
                except Exception as e:
                    logger.error(f"Lỗi khi thiết lập download behavior: {e}")

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

                set_video_file_background(page, video_path, trigger_tiktok_file_chooser)
                logger.info(f"[Profile {profile_id}] Da chon video TikTok bang Playwright o che do nen.")

                # 2. Nhập mô tả & Hashtags
                time.sleep(10)
                caption_box = None
                for sel in ['div[class*="notranslate"][contenteditable="true"]', 'div[data-contents="true"]', '.public-DraftEditor-content']:
                    try:
                        caption_box = page.wait_for_selector(sel, timeout=5000)
                        if caption_box:
                            break
                    except Exception:
                        continue

                if caption_box:
                    caption_box.click()
                    time.sleep(1)
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    time.sleep(1)
                    
                    if tiktok_cfg.get("use_original_desc", False):
                        desc_text = str(video.desc or "").strip()
                    else:
                        desc_text = str(profile.get("_runtime_caption") or video.desc or "").strip()
                    _type_caption_with_tiktok_hashtags(page, desc_text)
                    console.print(f"   ➔ Đã nhập mô tả thành công.")
                    page.mouse.click(10, 10)
                    time.sleep(1)
                else:
                    console.print("[yellow]   ➔ Cảnh báo: Không tìm thấy ô nhập mô tả.[/]")

                # 3. Kiểm tra bản quyền
                console.print("   ➔ Đang thực hiện kéo xuống cuối trang...")
                page.mouse.click(475, 579)
                time.sleep(1)
                page.keyboard.press("End")
                time.sleep(3)
                console.print("   ➔ Đã xuống cuối trang. Đang chờ TikTok quét bản quyền...")
                time.sleep(5)

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
                        console.print("[green]   ➔ OK: Đã hiện thông báo AN TOÀN.[/]")
                        break
                    elif fail_el.is_visible():
                        is_safe = False
                        console.print("[red]   ➔ LỖI: Đã hiện thông báo BỊ HẠN CHẾ! Bỏ qua video.[/]")
                        break

                    if wait_check % 30 == 0:
                        console.print(f"      ...Đang đợi quét bản quyền ({wait_check}s)...")

                    time.sleep(5)
                    wait_check += 5

                if is_safe:
                    # 4. Bấm Đăng
                    console.print("   ➔ Đang tìm nút ĐĂNG...")
                    post_btn = page.locator('button:has-text("Post"), button:has-text("Đăng")').last
                    try:
                        post_btn.scroll_into_view_if_needed()
                        time.sleep(1)
                    except Exception as e:
                        logger.error(f"Lỗi scroll nút Đăng: {e}")

                    wait_u = 0
                    while wait_u < 300:
                        if post_btn.is_enabled():
                            break
                        time.sleep(2)
                        wait_u += 2

                    time.sleep(2)
                    post_btn.click(force=True)
                    console.print("[bold green]✅ Đã nhấn nút ĐĂNG thành công![/]")
                    time.sleep(15)
                    
                else:
                    logger.warning(f"[Profile {profile_id}] Video {video.aweme_id} bị hạn chế bản quyền, không đăng.")


            except Exception as e:
                logger.error(f"[Profile {profile_id}] Lỗi trong tiến trình upload TikTok: {e}")
                record_browser_diagnostic(
                    page=page,
                    profile_id=profile_id,
                    video_id=str(video.aweme_id),
                    platform="tiktok",
                    error=e,
                    url=UPLOAD_URL,
                    last_response=response_trace,
                )
            finally:
                try:
                    _close_page_quietly(page)
                except Exception:
                    pass

        return is_safe
