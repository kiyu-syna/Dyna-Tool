import requests, time, gspread, re, os, sys, random
from datetime import datetime, timedelta
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.rule import Rule
import threading
from telegram_manager import send_upload_success_notification

# Import từ các module mới
from config import *
from sheets_manager import connect_and_style_sheets
from utils import bring_window_to_top, logger, console, notify_intervention, arrange_window_for_profile
from browser_manager import is_captcha_present
# ==========================================================
# API_URL, PROFILE_IDS are imported from config
UPLOAD_URL   = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
CLICK_UPLOAD_X, CLICK_UPLOAD_Y = 681, 607

# Quản lý các profile đang bận upload (Thread-safe)
_busy_profiles = set()
_busy_lock = threading.Lock()

# ==========================================================
# CÁC HÀM XỬ LÝ
# ==========================================================

def extract_id(url):
    match = re.search(r'video/(\d+)', str(url))
    if match: return match.group(1)
    match2 = re.search(r'(?:aweme_id|gid|group_id)=(\d+)', str(url))
    if match2: return match2.group(1)
    return None

def get_video_path(profile_id, video_id):
    """Lấy đường dẫn file video dựa trên SAVE_DIR của từng profile."""
    import config as _cfg
    pcfg = _cfg.get_profile_settings(profile_id)
    dir_path = pcfg.get("SAVE_DIR", "")
    if not dir_path: return None
    file_path = os.path.join(dir_path, f"{video_id}.mp4")
    return file_path if os.path.exists(file_path) else None

def parse_schedule_time(value: str):
    """
    Parse chuỗi thời gian từ cột LỊCH ĐĂNG.
    Hỗ trợ nhiều định dạng, ưu tiên định dạng Việt Nam DD/MM/YYYY.
    """
    value = str(value).strip().lstrip("'").replace("h", ":").replace("H", ":") # Hỗ trợ 11h30
    if not value or value.lower() in ["nan", "none", "null"] or "xx:xx" in value:
        return None

    # Các định dạng có cả ngày lẫn giờ
    # Ưu tiên d/m/Y (Việt Nam) trước m/d/Y (US)
    datetime_formats = [
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%H:%M %d/%m/%Y",
        "%H:%M:%S %d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
    ]
    for fmt in datetime_formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    # Các định dạng chỉ có giờ → dùng ngày hôm nay
    # Nếu giờ đã qua, có thể người dùng muốn đăng vào ngày mai? 
    # Nhưng hiện tại cứ để là hôm nay cho đơn giản, hoặc log cảnh báo.
    if ":" in value:
        time_parts = value.split(":")
        if len(time_parts) >= 2:
            try:
                # Chỉ lấy HH:MM
                h = int(time_parts[0])
                m = int(time_parts[1])
                dt = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
                return dt
            except ValueError:
                pass

    # Các định dạng chỉ có ngày → giờ mặc định 00:00
    date_only_formats = [
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%Y-%m-%d",
    ]
    for fmt in date_only_formats:
        try:
            dt = datetime.strptime(value, fmt)
            return datetime.combine(dt.date(), datetime.min.time())
        except ValueError:
            continue

    return None


# ==========================================================
# HÀM UPLOAD 1 VIDEO (dùng browser đang mở)
# ==========================================================

def _upload_one_video(context, sheet, row_idx, row_data, idx_map, profile_id="1"):
    """
    Upload 1 video cụ thể lên TikTok.
    context: playwright browser context
    sheet: gspread worksheet object
    row_idx: số hàng (1-indexed) trong sheet
    row_data: list giá trị của hàng đó
    idx_map: dict {tên_cột: index} để tra cứu nhanh
    profile_id: ID của profile để xác định vị trí grid
    """
    idx_hashtag  = idx_map["HASHTAG"]
    idx_link     = idx_map["LINK VIDEO"]
    idx_desc     = idx_map["MÔ TẢ"]
    idx_status   = idx_map["TRẠNG THÁI"]
    idx_time     = idx_map["POST TIME"]

    hashtag     = row_data[idx_hashtag]
    video_link  = row_data[idx_link]
    video_id    = extract_id(video_link)
    description = row_data[idx_desc] if idx_desc < len(row_data) else ""
    file_path   = get_video_path(profile_id, video_id)

    if not file_path:
        console.print(f"[red]❌ Thiếu file cho ID {video_id}[/]")
        sheet.update_cell(row_idx, idx_status + 1, "Thiếu file")
        return False

    console.print(f"[bold cyan]🎬 Đang xử lý: {video_id} (#{hashtag})[/]")
    sheet.update_cell(row_idx, idx_status + 1, "Đang đăng")

    # Thử lấy page hiện tại nếu có, nếu không thì tạo mới
    pages = context.pages
    if pages:
        page = pages[0]
    else:
        page = context.new_page()
        
    is_safe = False
    try:
        console.print(f"   ➔ Đang truy cập link upload: {UPLOAD_URL}")
        page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
        
        try:
            p_idx = PROFILE_IDS.index(str(profile_id))
            arrange_window_for_profile(page, p_idx)
        except Exception as e:
            logger.debug(f"Không thể sắp xếp grid cho upload: {e}")
            
        page.bring_to_front()
        bring_window_to_top(['TikTok', 'Iron Browser', 'GemLogin', 'AdsPower'], maximize=True)
        time.sleep(5)

        # --- KIỂM TRA CAPTCHA ---
        if is_captcha_present(page):
            console.print("[bold red]🚨 Phát hiện CAPTCHA khi upload! Đang gửi thông báo...[/]")
            notify_intervention("Phát hiện CAPTCHA trên TikTok Studio. Vui lòng xử lý để tiếp tục upload.", page)
            # Tạm dừng cho đến khi captcha biến mất
            while is_captcha_present(page):
                time.sleep(5)
            console.print("[bold green]✅ CAPTCHA đã được xử lý. Tiếp tục...[/]")

        # 1. Mở cửa sổ chọn file
        try:
            client = page.context.new_cdp_session(page)
            client.send("Browser.setDownloadBehavior", {"behavior": "default"})
        except Exception as e:
            logger.error(f"Lỗi khi thiết lập download behavior: {e}")

        bring_window_to_top(['TikTok', 'Iron Browser', 'GemLogin', 'AdsPower'], maximize=True)
        page.mouse.click(CLICK_UPLOAD_X, CLICK_UPLOAD_Y)
        time.sleep(3)

        import pygetwindow as gw
        import pyautogui
        import pyperclip

        # Debug: In ra tất cả tiêu đề cửa sổ đang mở theo yêu cầu của USER
        console.print("[dim]🔍 Danh sách các cửa sổ đang mở:[/dim]")
        for w in gw.getAllTitles():
            if w.strip():
                print(f"   > {w}")

        # Đảm bảo cửa sổ chọn file hiện lên cao nhất để paste
        dialog_found = False
        for _ in range(5):
            if bring_window_to_top(['Open', 'Mở', 'Chọn tập tin', 'File Upload', 'Upload'], topmost=True):
                dialog_found = True
                break
            time.sleep(1)

        if dialog_found:
            time.sleep(1) # Đợi thêm 1 chút cho cửa sổ ổn định
            try:
                # Lấy tọa độ cửa sổ đang active (vừa được bring_window_to_top)
                active_win = gw.getActiveWindow()
                if active_win:
                    # Click vào vùng nhập liệu (thường nằm ở nửa dưới dialog)
                    # Hoặc đơn giản là click vào giữa để lấy focus
                    center_x = active_win.left + active_win.width // 2
                    center_y = active_win.top + active_win.height // 2
                    pyautogui.click(center_x, center_y)
                    console.print(f"   ➔ Đã click vào cửa sổ {active_win.title} để lấy focus.")
            except Exception as e:
                logger.error(f"Lỗi khi click vào cửa sổ chọn file để lấy focus: {e}")
                pyautogui.click(640, 360) 
            
            time.sleep(0.5)
            # Xóa trắng ô nhập liệu trước khi paste để tránh bị dính nội dung cũ
            pyautogui.hotkey('ctrl', 'a')
            pyautogui.press('backspace')
            time.sleep(0.5)

        pyperclip.copy(file_path)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(1)
        pyautogui.press('enter')

        # 2. Nhập mô tả & Hashtag
        time.sleep(10)
        caption_box = None
        for sel in ['div[class*="notranslate"][contenteditable="true"]', 'div[data-contents="true"]', '.public-DraftEditor-content']:
            try:
                caption_box = page.wait_for_selector(sel, timeout=5000)
                if caption_box: break
            except Exception:
                continue

        if caption_box:
            caption_box.click()
            time.sleep(1)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            time.sleep(1)
            clean_tag = hashtag.replace("#", "")
            full_desc = f"{description} #{clean_tag} #fyp #xuhuong #xh #kiyu "
            page.keyboard.type(full_desc, delay=50)
            console.print(f"   ➔ Đã nhập mô tả thành công.")
            page.mouse.click(10, 10)
            time.sleep(1)
        else:
            console.print("[yellow]   ➔ Cảnh báo: Không tìm thấy ô nhập mô tả.[/]")

        # 3. Chờ kiểm tra bản quyền
        console.print("   ➔ Đang thực hiện kéo xuống cuối trang...")
        page.mouse.click(475, 579)
        time.sleep(1)
        page.keyboard.press("End")
        time.sleep(3)
        console.print("   ➔ Đã xuống cuối trang. Đang chờ TikTok trả kết quả quét...")
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
                logger.error(f"Lỗi khi scroll đến nút Đăng: {e}")

            wait_u = 0
            while wait_u < 300:
                if post_btn.is_enabled(): break
                time.sleep(2)
                wait_u += 2

            time.sleep(2)
            post_btn.click(force=True)
            console.print("[bold green]✅ Đã nhấn nút ĐĂNG thành công![/]")

            time.sleep(15)
            sheet.update_cell(row_idx, idx_status + 1, "Đã đăng")
            sheet.update_cell(row_idx, idx_time + 1, datetime.now().strftime("%H:%M:%S %d/%m/%Y"))

            send_upload_success_notification(video_id, hashtag)
        else:
            sheet.update_cell(row_idx, idx_status + 1, "Bị hạn chế (Bỏ qua)")

    except Exception as e:
        console.print(f"[bold red]❌ Lỗi: {e}[/]")
        sheet.update_cell(row_idx, idx_status + 1, "Lỗi upload")
    finally:
        try:
            page.close()
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                console.print(f"[dim]   ➔ Đã xóa file: {os.path.basename(file_path)}[/dim]")
        except Exception as e:
            logger.error(f"Lỗi khi đóng page hoặc xóa file: {e}")

    return is_safe

# ==========================================================
# VÒNG LẶP SCHEDULER CHÍNH
# ==========================================================

def schedule_upload_loop():
    """
    Chạy ngầm liên tục, mỗi 30 giây quét Google Sheet của TỪNG PROFILE 1 lần.
    Tìm video có LỊCH ĐĂNG đã đến giờ → tự động upload bằng đúng profile đó.
    """
    logger.info("[bold green]⏰ Scheduler Upload đã khởi động. Đang chờ lịch từ Google Sheet của từng Profile...[/bold green]")

    import config as _config_module
    from utils import normalize_column_name

    while True:
        try:
            now = datetime.now()

            # Duyệt qua từng profile và kiểm tra sheet tab riêng của nó
            for p_id in PROFILE_IDS:
                try:
                    pcfg = _config_module.get_profile_settings(p_id)
                    p_sheet_url = pcfg.get("GOOGLE_SHEET_URL", "")

                    sheet = connect_and_style_sheets(sheet_url=p_sheet_url)
                    if not sheet:
                        logger.warning(f"[yellow]⚠️ Profile {p_id}: Không kết nối được Sheet.[/yellow]")
                        continue

                    all_rows = sheet.get_all_values()
                    if len(all_rows) < 2:
                        continue

                    headers = all_rows[0]
                    clean_headers = [normalize_column_name(h) for h in headers]

                    # Danh sách cột cần thiết (đã chuẩn hóa)
                    required_cols = {
                        "HASHTAG": "HASHTAG",
                        "LINK VIDEO": "LINKVIDEO",
                        "TẢI VỀ": "TAIVE",
                        "MÔ TẢ": "MOTA",
                        "TRẠNG THÁI": "TRANGTHAI",
                        "POST TIME": "POSTTIME",
                        "LỊCH ĐĂNG": "LICHDANG"
                    }
                    
                    idx_map = {}
                    missing = []
                    for display_name, norm_name in required_cols.items():
                        try:
                            idx_map[display_name] = clean_headers.index(norm_name)
                        except ValueError:
                            missing.append(display_name)

                    if missing:
                        logger.warning(f"[yellow]⚠️ Profile {p_id}: Thiếu cột trong Sheet: {missing}[/yellow]")
                        continue

                    # Lấy danh sách video cần upload
                    videos_to_upload = []
                    for i, row in enumerate(all_rows[1:], start=2):
                        # Pad row nếu thiếu cột
                        row = list(row) + [""] * (len(headers) - len(row))

                        val_downloaded = str(row[idx_map["TẢI VỀ"]]).strip().upper()
                        is_downloaded  = val_downloaded in ["TRUE", "V", "X", "☑", "✅"]
                        status         = str(row[idx_map["TRẠNG THÁI"]]).strip().lower()
                        lich_dang_raw  = str(row[idx_map["LỊCH ĐĂNG"]]).strip()

                        if status == "đã đăng":
                            continue
                        
                        if not is_downloaded:
                            continue

                        if not lich_dang_raw:
                            continue

                        scheduled_time = parse_schedule_time(lich_dang_raw)
                        if scheduled_time is None:
                            logger.warning(f"[yellow]⚠️ Profile {p_id} Dòng {i}: Không thể parse thời gian '{lich_dang_raw}'[/yellow]")
                            continue

                        deadline = scheduled_time + timedelta(minutes = 60)

                        if scheduled_time <= now <= deadline:
                            logger.info(f"[bold green]🔔 Profile {p_id} Dòng {i}: Đến giờ đăng! ({lich_dang_raw})[/bold green]")
                            videos_to_upload.append((i, row))
                        elif now > deadline:
                            logger.warning(f"[yellow]⚠️ Profile {p_id} Dòng {i}: Đã trễ hơn 60 phút so với lịch ({lich_dang_raw}) -> Bỏ qua.[/yellow]")
                            try:
                                sheet.update_cell(i, idx_map["TRẠNG THÁI"] + 1, "Bỏ qua - Trễ giờ")
                                video_link = row[idx_map["LINK VIDEO"]]
                                video_id = extract_id(video_link)
                                file_path = get_video_path(p_id, video_id)
                                if file_path and os.path.exists(file_path):
                                    os.remove(file_path)
                            except Exception as e:
                                logger.error(f"Lỗi khi cập nhật trạng thái hoặc xóa file video đã trễ: {e}")

                    if not videos_to_upload:
                        continue

                    # Upload từng video trong danh sách, dùng đúng profile của tab này
                    for row_idx, row_data in videos_to_upload:
                        # Kiểm tra profile đang bận không
                        with _busy_lock:
                            if p_id in _busy_profiles:
                                logger.info(f"[dim]Scheduler: Profile {p_id} đang bận, bỏ qua dòng {row_idx}...[/dim]")
                                continue
                            _busy_profiles.add(p_id)

                        logger.info(f"[bold yellow]⏰ Scheduler: Kích hoạt upload dòng {row_idx} bằng Profile {p_id}...[/bold yellow]")

                        def do_upload(prof_id=p_id, r_idx=row_idx, r_data=row_data, sht=sheet, imap=idx_map):
                            try:
                                res = requests.get(f"{API_URL}/api/profiles/start/{prof_id}").json()
                                debug_addr = res.get("data", {}).get("remote_debugging_address")
                                if not debug_addr:
                                    logger.error(f"[red]❌ Profile {prof_id}: Không lấy được debug address![/red]")
                                    return
                                with sync_playwright() as pw:
                                    browser = pw.chromium.connect_over_cdp(f"http://{debug_addr}")
                                    context = browser.contexts[0]
                                    logger.info("==========================================================")
                                    logger.info(f"   BẮT ĐẦU TIẾN TRÌNH UPLOAD TIKTOK - PROFILE {prof_id}   ")
                                    logger.info("==========================================================")
                                    _upload_one_video(context, sht, r_idx, r_data, imap, str(prof_id))
                            except Exception as e:
                                logger.error(f"[bold red]❌ Lỗi scheduler upload Profile {prof_id}: {e}[/bold red]")
                            finally:
                                with _busy_lock:
                                    if prof_id in _busy_profiles:
                                        _busy_profiles.remove(prof_id)

                        threading.Thread(target=do_upload, daemon=True).start()
                        time.sleep(2)

                except Exception as e_prof:
                    logger.error(f"[bold red]❌ Scheduler lỗi khi xử lý Profile {p_id}: {e_prof}[/bold red]")

        except Exception as e:
            err_msg = str(e)
            logger.error(f"[bold red]❌ Lỗi vòng lặp scheduler: {err_msg}[/bold red]")
            if "connect_over_cdp" in err_msg or "Target closed" in err_msg:
                notify_intervention(f"TikTok Scheduler: Mất kết nối trình duyệt: {err_msg}")

        time.sleep(30)
