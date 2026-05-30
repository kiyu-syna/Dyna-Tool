import requests, time, os, sys, random
from datetime import datetime
from playwright.sync_api import sync_playwright
import pyautogui
from rich.rule import Rule

# Import từ các module mới
from config import *
import config as _config_module
from utils import (
    console, logger, extract_id, parse_duration, parse_count, log, 
    get_random_description, bring_window_to_top, notify_intervention,
    arrange_window_for_profile
)
from sheets_manager import connect_and_style_sheets
from browser_manager import (
    extract_id_from_page, 
    get_active_video_data, 
    video_has_target_tag, 
    is_live_video,
    is_captcha_present
)
import threading
from telegram_manager import start_telegram_bot, send_video_notification
from shared_state import DOUYIN_CONTROL
import stats_manager


# ==========================================================
# HÀM HỖ TRỢ CHẠY NGẦM VÀ ĐỒNG BỘ
# ==========================================================
_sheet_write_lock = threading.Lock()


# TIẾN TRÌNH CHO TừNG PROFILE RIÊNG BIỆT
# ===========================================================
def _run_automation_single_profile(profile_id, shared_state_obj, profile_index=0):
    # Tải cấu hình riêng của profile này
    pcfg = _config_module.get_profile_settings(profile_id)
    p_target_tags    = pcfg["TARGET_TAGS"]
    p_min_likes      = pcfg["MIN_LIKES"]
    p_max_duration   = pcfg["MAX_DURATION"]
    p_max_new_videos = pcfg["MAX_NEW_VIDEOS"]
    p_save_dir       = pcfg.get("SAVE_DIR", "")
    p_sheet_url      = pcfg["GOOGLE_SHEET_URL"]

    # Kết nối Google Sheet của profile này
    sheet = connect_and_style_sheets(sheet_url=p_sheet_url)
    if not sheet:
        logger.error(f"[bold red]❌ [Profile {profile_id}] Không kết nối được Google Sheet![/]")
        return

    # Đồng bộ ID đã quét từ tab riêng
    try:
        all_rows = sheet.get_all_values()
        profile_scanned = [extract_id(r[1]) for r in all_rows[1:] if extract_id(r[1])]
        console.print(f"[green]✅ [Profile {profile_id}] Đã đồng bộ {len(profile_scanned)} ID từ tab riêng.[/]")
    except Exception as e:
        logger.error(f"[bold red]❌ [Profile {profile_id}] Lỗi đồng bộ ID từ sheet: {e}[/]")
        profile_scanned = []

    with shared_state_obj.lock:
        for vid in profile_scanned:
            if vid not in shared_state_obj.scanned_ids:
                shared_state_obj.scanned_ids.append(vid)

    mode = DOUYIN_CONTROL["mode"] # 0: Fyp, 1: Follow
    
    try:
        logger.info(f"[Profile {profile_id}] Requesting browser profile from API: {API_URL}")
        response = requests.get(f"{API_URL}/api/profiles/start/{profile_id}", timeout=30)
        response.raise_for_status()
        res = response.json()
        debug_addr = res.get("data", {}).get("remote_debugging_address")
        if not debug_addr:
            logger.error(f"[bold red]âŒ [Profile {profile_id}] API did not return remote_debugging_address. Raw response: {res}[/]")
        if not debug_addr:
            logger.error(f"❌ [Profile {profile_id}] Không lấy được remote debugging address từ GemLogin!")
            return

        with sync_playwright() as pw:
            logger.info(f"[Profile {profile_id}] Connecting to CDP at http://{debug_addr}")
            browser = pw.chromium.connect_over_cdp(f"http://{debug_addr}")
            if not browser.contexts:
                logger.error(f"[bold red]âŒ [Profile {profile_id}] CDP Ä‘Ã£ káº¿t ná»‘i nhÆ°ng khÃ´ng cÃ³ browser context nÃ o.[/]")
                return
            context = browser.contexts[0]

            # Thiết lập URL và số tab tùy theo chế độ
            target_url = HOME_URL if mode == 0 else "https://www.douyin.com/follow"
            active_tabs = 1

            # Mở tab
            pages_info = []
            for i in range(active_tabs):
                page = context.new_page()
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                    arrange_window_for_profile(page, profile_index)
                    time.sleep(random.uniform(4, 6))
                    
                    # Bỏ logic check Live lúc mở tab vì đã có check ở vòng lặp chính

                    
                    pages_info.append({"tab": i + 1, "page": page})
                    console.print(f"[cyan]🟢 [Profile {profile_id}] Đã mở tab {i + 1}: {target_url}[/]")
                except Exception as e:
                    logger.error(f"[bold red]❌ [Profile {profile_id}] Lỗi khi mở tab {i+1}: {e}[/]")
                    # Nếu có lỗi khi mở tab, không thêm vào pages_info và tiếp tục với tab khác
                    page.close()


            if mode == 0:
                console.print(Rule(f"[bold cyan]🚀 [Profile {profile_id}] BẮT ĐẦU QUÉT TRANG ĐỀ XUẤT[/bold cyan]"))
            else:
                console.print(Rule(f"[bold cyan]🚀 [Profile {profile_id}] BẮT ĐẦU QUÉT KÊNH FOLLOW[/bold cyan]"))

            while True:
                # Kiểm tra trạng thái chạy từ Telegram (Nếu đang chạy mà bị stop)
                with DOUYIN_CONTROL._lock: # Sử dụng lock khi truy cập DOUYIN_CONTROL
                    if not DOUYIN_CONTROL["running"]:
                        console.print(f"[yellow]🛑 [Profile {profile_id}] Đã nhận lệnh dừng quét.[/]")
                        break # Thoát ngay, GUI sẽ lo đóng cửa sổ

                for item in pages_info:
                    tab_num, p_obj = item["tab"], item["page"]
                    v_id = None
                    like_t, dur_t = "0", "00:00"
                    matched_tag = "unknown"
                    try:
                        # Kiểm tra dừng ngay trước khi chuyển tab
                        with DOUYIN_CONTROL._lock: # Sử dụng lock khi truy cập DOUYIN_CONTROL
                            if not DOUYIN_CONTROL["running"]:
                                break
                            
                        p_obj.bring_to_front()
                        time.sleep(random.uniform(1, 2))

                        # --- KIỂM TRA CAPTCHA ---
                        if is_captcha_present(p_obj):
                            console.print(f"[bold red]🚨 [Profile {profile_id}] Phát hiện CAPTCHA! Đang gửi thông báo...[/]")
                            notify_intervention(f"Phát hiện CAPTCHA trên Douyin (Profile {profile_id}). Vui lòng xử lý để tiếp tục quét.", p_obj)
                            # Tạm dừng quét cho đến khi captcha biến mất hoặc người dùng nhấn dừng
                            while True:
                                with DOUYIN_CONTROL._lock: # Sử dụng lock khi truy cập DOUYIN_CONTROL
                                    if not DOUYIN_CONTROL["running"]:
                                        break
                                if not is_captcha_present(p_obj):
                                    break
                                time.sleep(5)
                            if not DOUYIN_CONTROL["running"]: break
                            console.print(f"[bold green]✅ [Profile {profile_id}] CAPTCHA đã được xử lý. Tiếp tục quét...[/]")

                        with DOUYIN_CONTROL._lock: # Sử dụng lock khi truy cập DOUYIN_CONTROL
                            if not DOUYIN_CONTROL["running"]:
                                break

                        if is_live_video(p_obj):
                            log(f"prof{profile_id}-tab{tab_num}", "—", "—", "—", "⏭️  Video LIVE (Bỏ qua)", "dim yellow")
                            p_obj.keyboard.press("ArrowDown") # Quẹt xuống để bỏ qua video live
                            time.sleep(random.uniform(0.5, 1.0))
                            continue

                        v_id = extract_id_from_page(p_obj)
                        if not v_id:
                            # Nếu không tìm thấy ID (có thể là một định dạng Live lạ, hoặc video quảng cáo bị lỗi)
                            # Tuyệt đối KHÔNG click chuột vào màn hình vì có thể bấm nhầm vào nút xem Live
                            log(f"prof{profile_id}-tab{tab_num}", "—", "—", "—", "⏭️  Không tìm thấy ID Video (Bỏ qua)", "dim yellow")
                            continue

                        v_data = get_active_video_data(p_obj)
                        description = v_data.get("description", "")
                        
                        if mode == 0:
                            matched_tag = video_has_target_tag(description, p_target_tags)
                        else:
                            matched_tag = "FOLLOW"
                            if p_target_tags:
                                matched_tag = p_target_tags[0]

                        if not matched_tag and mode == 0:
                            log(f"prof{profile_id}-tab{tab_num}", v_id, "—", "—", "⏭️  Không có hashtag mục tiêu", "dim")
                        else:
                            like_t = v_data.get("likes", "0")
                            dur_t = v_data.get("duration", "00:00")
                            dur_found = (dur_t != "00:00")

                            l_count = parse_count(like_t)
                            d_sec   = parse_duration(dur_t)
                            dur_ok  = (not dur_found) or (0 < d_sec < p_max_duration)
                            
                            is_qualified = False
                            if mode == 0:
                                is_qualified = (v_id and dur_ok and l_count >= p_min_likes)
                            else:
                                is_qualified = (v_id and dur_ok)

                            # Đọc/ghi shared_state_obj cần đồng bộ hóa
                            with shared_state_obj.lock:
                                is_already_scanned = v_id in shared_state_obj.scanned_ids
                                should_save = is_qualified and not is_already_scanned
                                if should_save:
                                    # Đánh dấu đã quét ngay lập tức để luồng khác không quét trùng
                                    shared_state_obj.scanned_ids.append(v_id)

                            if is_already_scanned:
                                log(matched_tag, v_id, like_t, dur_t, "⚠️  Đã có", "dim")
                            elif should_save:
                                # Kiểm tra xem đã đủ số lượng video chưa
                                with shared_state_obj.lock:
                                    if shared_state_obj.new_found_count >= p_max_new_videos:
                                        console.print(f"[bold green]🏁 [Profile {profile_id}] Đã tìm đủ {p_max_new_videos} video. Dừng luồng.[/bold green]")
                                        return
                                    shared_state_obj.new_found_count += 1
                                    current_new_count = shared_state_obj.new_found_count
                                
                                final_desc = get_random_description()
                                console.print(f"[bold green]   ➔ [Profile {profile_id}] Mô tả mới: {final_desc}[/]")
                                
                                clean_link = f"https://www.douyin.com/video/{v_id}"
                                default_lich_dang = datetime.now().strftime("'%d/%m/%Y xx:xx")
                                
                                with _sheet_write_lock:
                                    sheet.append_row([matched_tag, clean_link, dur_t, like_t, datetime.now().strftime("%H:%M:%S"), "FALSE", final_desc, "Chưa đăng", "", default_lich_dang])
                                
                                stats_manager.increment_stat("scanned")
                                log(matched_tag, v_id, like_t, dur_t, f"✅ ĐÃ LƯU ({current_new_count}/{p_max_new_videos})", "bold green")
                                
                                # Gửi thông báo Telegram
                                send_video_notification(matched_tag, clean_link, final_desc)
                                
                                if current_new_count >= p_max_new_videos:
                                    console.print(Rule(f"[bold green]🏁 [Profile {profile_id}] ĐÃ TÌM ĐỦ {p_max_new_videos} VIDEO. DừNG TIẾN TRÌNH.[/bold green]"))
                                    with DOUYIN_CONTROL._lock: # Sử dụng lock khi truy cập DOUYIN_CONTROL
                                        DOUYIN_CONTROL["running"] = False
                                    return 
                                
                            else:
                                reason = f"id={'Y' if v_id else 'N'}"
                                if mode == 0:
                                    reason += f" tim={int(l_count)}/{p_min_likes} dur_ok={dur_ok}"
                                else:
                                    reason += f" dur_ok={dur_ok}"
                                log(matched_tag, v_id, like_t, dur_t, f"⏭️  Skip │ {reason}", "dim cyan")

                    except Exception as ex:
                        if str(ex) != "STOP_SCAN":
                            logger.error(f"[bold red]❌ [Profile {profile_id}] Lỗi trong vòng lặp quét: {ex}[/bold red]")
                            log(f"prof{profile_id}-tab{tab_num}", v_id, like_t, dur_t, f"🔄 {type(ex).__name__}", "yellow")

                    finally:
                        try:
                            with DOUYIN_CONTROL._lock: # Sử dụng lock khi truy cập DOUYIN_CONTROL
                                if DOUYIN_CONTROL["running"]:
                                    p_obj.keyboard.press("ArrowDown")
                        except Exception as e:
                            logger.error(f"[red]   ➔ [Profile {profile_id}] Lỗi khi nhấn ArrowDown: {e}[/]")
                        with DOUYIN_CONTROL._lock: # Sử dụng lock khi truy cập DOUYIN_CONTROL
                            if DOUYIN_CONTROL["running"]:
                                time.sleep(random.uniform(0.5, 1.0))

    except Exception as e:
        logger.error(f"[bold red]❌ Lỗi ở Profile {profile_id}: {e}[/bold red]")

# ==========================================================
# LUỒNG VẬN HÀNH CHÍNH (ĐA LUỒNG)
# ==========================================================
def run_automation():
    with DOUYIN_CONTROL._lock: # Sử dụng lock khi truy cập DOUYIN_CONTROL
        if not DOUYIN_CONTROL["running"]:
            return # Tránh trường hợp vừa khởi chạy đã bị ấn dừng

    # Mỗi luồng tự kết nối Sheet riêng của nó.
    # shared_state_obj chỉ để đồng bộ danh sách scanned_ids và đếm video mới tìm được.
    class CrawlerState:
        def __init__(self):
            self.scanned_ids = []
            self.new_found_count = 0
            self.lock = threading.Lock()

    shared_state_obj = CrawlerState()
    p_ids = PROFILE_IDS
    console.print(f"[bold cyan]🚀 Bắt đầu quét song song trên {len(p_ids)} Profile: {p_ids}[/]")

    try:
        import pyautogui
        pyautogui.hotkey('win', 'm')
        time.sleep(0.5)
    except Exception as e:
        pass

    threads = []
    for i, p_id in enumerate(p_ids):
        t = threading.Thread(
            target=_run_automation_single_profile,
            args=(p_id, shared_state_obj, i),
            daemon=True
        )
        threads.append(t)
        t.start()
        # Tránh mở đồng thời tất cả profile quá nhanh, để lệch nhau 3 giây
        time.sleep(3)

    # Đợi cho tất cả các luồng kết thúc
    for t in threads:
        t.join()

    with DOUYIN_CONTROL._lock: # Sử dụng lock khi truy cập DOUYIN_CONTROL
        DOUYIN_CONTROL["running"] = False
    console.print("[dim]🛑 Luồng quét Douyin đã kết thúc ở tất cả các Profile.[/dim]")

if __name__ == "__main__":
    # ==========================================================
    # KHỞI CHẠY DASHBOARD (NẾU CHẠY LẺ FILE NÀY)
    # ==========================================================
    if os.name == 'nt' and "DASHBOARD_ACTIVE" not in os.environ:
        os.environ["DASHBOARD_ACTIVE"] = "1"
        os.system(f'start "DOUYIN MONITOR" {sys.executable} "{__file__}"')
        sys.exit()

    # Khởi chạy Telegram Bot Listener khi chạy lẻ file
    threading.Thread(target=start_telegram_bot, daemon=True).start()
    run_automation()
    console.print(Rule("[bold magenta]🚀 CHUYỂN SANG TIẾN TRÌNH UPLOAD TIKTOK[/bold magenta]"))
    os.system(f'start "TIKTOK UPLOAD" {sys.executable} "upload_tiktok.py"')
