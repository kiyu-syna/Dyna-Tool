import re, ctypes, os, random, sys, unicodedata, threading
from datetime import datetime
from rich.console import Console
import core.config as config
import pygetwindow as gw
from deep_translator import GoogleTranslator # Import thư viện dịch

# ==========================================================
# FIX LỖI SSL/TLS CHO FILE EXE (PYINSTALLER)
# ==========================================================
if getattr(sys, 'frozen', False):
    # Nếu chạy từ EXE, bundle certifi vào và ép requests dùng nó
    cert_path = os.path.join(sys._MEIPASS, 'certifi', 'cacert.pem')
    if os.path.exists(cert_path):
        os.environ['SSL_CERT_FILE'] = cert_path
        os.environ['REQUESTS_CA_BUNDLE'] = cert_path

def normalize_column_name(text):
    """Chuẩn hóa tên cột để so khớp không phân biệt hoa thường/dấu/khoảng trắng."""
    if not text: return ""
    text = str(text).strip().upper()
    # Đặc biệt xử lý chữ Đ/đ vì NFKD không tách dấu của nó
    text = text.replace("Đ", "D").replace("đ", "D")
    # Loại bỏ dấu tiếng Việt và chuẩn hóa Unicode
    text = unicodedata.normalize('NFKD', text)
    text = "".join([c for c in text if not unicodedata.combining(c)])
    return text.replace(" ", "")

import logging
from logging.handlers import RotatingFileHandler
from rich.logging import RichHandler

console = Console()

# Cấu hình Logging tập trung
log_dir = os.path.join(config.BASE_DIR, "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "system.log")

logging.basicConfig(
    level="INFO",
    format="%(message)s",
    datefmt="[%X]",
    handlers=[
        RichHandler(rich_tracebacks=True, markup=True, console=console),
        RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    ]
)
logger = logging.getLogger("rich")

def extract_id(url):
    # Tìm theo dạng /video/12345
    match = re.search(r'video/(\d+)', str(url))
    if match: return match.group(1)
    
    # Tìm theo dạng aweme_id=12345 hoặc gid=12345 (trên trang đề xuất)
    match2 = re.search(r'(?:aweme_id|gid|group_id)=(\d+)', str(url))
    if match2: return match2.group(1)
    
    return None

def parse_duration(t):
    try:
        t = re.sub(r'[^\d:]', '', t.strip())
        p = t.split(':')
        if len(p) == 2: return int(p[0]) * 60 + int(p[1])
        if len(p) == 3: return int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2])
        return 0
    except Exception as e:
        logger.debug(f"Lỗi parse_duration: {e}")
        return 0

def parse_count(s):
    try:
        s = s.strip().replace(',', '')
        if '万' in s or (s.lower().endswith('w') and '万' not in s):
            nums = re.findall(r"\\d+\\.?\\d*", s)
            return float(nums[0]) * 10000 if nums else 0
        if '千' in s or s.lower().endswith('k'):
            nums = re.findall(r"\\d+\\.?\\d*", s)
            return float(nums[0]) * 1000 if nums else 0
        clean = re.sub(r'[^\d.]', '', s)
        return float(clean) if clean else 0
    except Exception as e:
        logger.debug(f"Lỗi parse_count: {e}")
        return 0

def safe_text(locator, timeout=1000, fallback=""):
    try:
        return locator.inner_text(timeout=timeout)
    except Exception as e:
        logger.debug(f"Lỗi safe_text: {e}")
        return fallback

def log(tag, v_id, like_t, dur_t, result_label, result_style):
    ts = datetime.now().strftime("%H:%M:%S")
    vid_str = v_id if v_id else "-------"
    
    # In ra console
    console.print(
        f"[dim]{ts}[/dim] "
        f"[bold yellow]#{tag:<10}[/bold yellow] "
        f"[white]{vid_str:<22}[/white] "
        f"[bold red]❤ {like_t:<8}[/bold red] "
        f"[green]⏱ {dur_t:<7}[/green] "
        f"[{result_style}]{result_label}[/{result_style}]"
    )
    
    # Ghi vào file log (bỏ qua mã màu rich)
    log_msg = f"#{tag:<10} {vid_str:<22} L:{like_t:<8} D:{dur_t:<7} -> {result_label}"
    logger.info(log_msg)

def translate_description(text, target_lang='vi'):
    """
    Loại bỏ hashtag và dịch mô tả sang tiếng Việt (Dự phòng cho ChatGPT).
    """
    if not text: return ""
    try:
        clean_text = re.sub(r'#\S+', '', text)
        clean_text = clean_text.replace('展开', '').replace('收起', '').strip()
        if not clean_text: return ""
        
        translated = GoogleTranslator(source='auto', target=target_lang).translate(clean_text)
        return translated
    except Exception as e:
        console.print(f"[dim red]   ➔ Lỗi trình dịch dự phòng: {e}[/]")
        return text


def get_random_description():
    """Trả về ngẫu nhiên 1 trong 2 chuỗi icon theo yêu cầu của USER"""
    return random.choice(["😌😌😌😌", "😭💔🥀"])

import win32gui
import win32con
import time # Import time for sleep

_PROFILE_WINDOW_HWND = {}
_WINDOW_FOCUS_LOCK = threading.RLock()


def get_window_focus_lock():
    return _WINDOW_FOCUS_LOCK


def _maximize_window(hwnd):
    """Maximize directly without restoring first, which visibly shrinks the window."""
    win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)


def _activate_window(hwnd, topmost=True, maximize=False):
    try:
        if maximize:
            _maximize_window(hwnd)
        elif win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass

    try:
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
        )
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetActiveWindow(hwnd)
        if maximize:
            _maximize_window(hwnd)
        if not topmost:
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_NOTOPMOST,
                0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
            )
    except Exception:
        raise


def _get_active_profile_count():
    try:
        profiles = config.load_profile_configs()
        active_profiles = [cfg for cfg in profiles.values() if cfg.get("enabled", True)]
        return max(1, len(active_profiles))
    except Exception:
        return 4


def _collect_browser_windows():
    browsers = []
    for win in gw.getAllWindows():
        if not win.title:
            continue
        title = win.title.lower()
        if any(x in title for x in ["visual studio", "cmd.exe", "powershell", "antigravity", ".py"]):
            continue
        if any(k in title for k in ["iron", "brower", "browser", "tiktok", "douyin", "??", "upload", "new tab", "gemlogin", "adspower"]):
            browsers.append(win)
    browsers.sort(key=lambda w: w._hWnd)
    return browsers


def _get_screen_grid_positions(total_profiles=None):
    screen_width = 1920
    screen_height = 1040
    total = max(1, int(total_profiles or _get_active_profile_count()))

    cols = 1
    while cols * cols < total:
        cols += 1
    rows = (total + cols - 1) // cols

    cell_w = max(320, screen_width // cols)
    cell_h = max(260, screen_height // rows)

    positions = []
    for row in range(rows):
        for col in range(cols):
            positions.append((col * cell_w, row * cell_h))

    return positions, cell_w, cell_h, cols, rows


def _window_matches_profile_title(win, profile_id):
    if not profile_id or not win.title:
        return False
    title = win.title.lower()
    pid = str(profile_id).strip().lower()
    tokens = [
        f"profile {pid}",
        f"profile_{pid}",
        f"gemlogin {pid}",
        f"adspower {pid}",
        f"#{pid}",
        f"[{pid}]",
        f"({pid})",
    ]
    return any(token in title for token in tokens)


def _window_distance_to_slot(win, profile_index, total_profiles=None):
    positions, cell_w, cell_h, _, _ = _get_screen_grid_positions(total_profiles=total_profiles)
    target_x, target_y = positions[int(profile_index) % len(positions)]
    center_x = win.left + max(win.width, 0) / 2
    center_y = win.top + max(win.height, 0) / 2
    target_center_x = target_x + cell_w / 2
    target_center_y = target_y + cell_h / 2
    return abs(center_x - target_center_x) + abs(center_y - target_center_y)


def _resolve_profile_window(profile_id=None, profile_index=None, total_profiles=None):
    browsers = _collect_browser_windows()
    if not browsers:
        return None

    if profile_id is not None:
        bound_hwnd = _PROFILE_WINDOW_HWND.get(str(profile_id))
        if bound_hwnd:
            for win in browsers:
                if win._hWnd == bound_hwnd:
                    return win

        for win in browsers:
            if _window_matches_profile_title(win, profile_id):
                _PROFILE_WINDOW_HWND[str(profile_id)] = win._hWnd
                return win

    if profile_index is not None:
        target_win = min(
            browsers,
            key=lambda win: _window_distance_to_slot(win, profile_index, total_profiles=total_profiles),
        )
        if profile_id is not None:
            _PROFILE_WINDOW_HWND[str(profile_id)] = target_win._hWnd
        return target_win

    return browsers[0]

def bring_window_to_top(title_keywords, topmost=True, maximize=False):
    """
    Tìm cửa sổ chứa các từ khóa trong tiêu đề và đưa nó lên trên cùng.
    Nếu topmost=True, nó sẽ luôn nằm trên các cửa sổ khác (Always on Top).
    """
    try:
        target_win = None
        all_wins = gw.getAllWindows()
        
        # Log ra tất cả tiêu đề nếu debug (người dùng yêu cầu print)
        # for w in gw.getAllTitles():
        #     if w.strip(): print(f"[DEBUG] Window: {w}")

        for win in all_wins:
            if any(kw.lower() in win.title.lower() for kw in title_keywords):
                target_win = win
                break
        
        if target_win:
            hwnd = target_win._hWnd
            console.print(f"[dim]   ➔ Đang đưa cửa sổ lên đầu: {target_win.title}[/]")
            
            # Phóng to hoặc phục hồi cửa sổ
            if maximize:
                _maximize_window(hwnd)
            elif win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                
            # Đưa lên foreground
            win32gui.SetForegroundWindow(hwnd)
            
            if topmost:
                # Thiết lập Always on Top bằng win32gui
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_TOPMOST,
                    0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
                )
            
            # Thêm một chút delay để Windows kịp xử lý
            time.sleep(0.5)
            return True
    except Exception as e:
        console.print(f"[dim red]   ➔ Lỗi khi đưa cửa sổ lên đầu: {e}[/]")
    return False


def bring_window_to_top(title_keywords, topmost=True, maximize=False):
    try:
        target_win = None
        for win in gw.getAllWindows():
            if any(kw.lower() in win.title.lower() for kw in title_keywords):
                target_win = win
                break

        if target_win:
            console.print(f"[dim]   -> Dang dua cua so len dau: {target_win.title}[/]")
            with _WINDOW_FOCUS_LOCK:
                _activate_window(target_win._hWnd, topmost=topmost, maximize=maximize)
                time.sleep(0.5)
            return True
    except Exception as e:
        console.print(f"[dim red]   -> Loi khi dua cua so len dau: {e}[/]")
    return False


def bring_profile_window_to_top(profile_id=None, profile_index=None, total_profiles=None, topmost=True, maximize=True):
    try:
        target_win = _resolve_profile_window(
            profile_id=profile_id,
            profile_index=profile_index,
            total_profiles=total_profiles,
        )
        if not target_win:
            return False

        hwnd = target_win._hWnd
        if profile_id is not None:
            _PROFILE_WINDOW_HWND[str(profile_id)] = hwnd

        console.print(f"[dim]   -> Dang dua cua so profile len dau: {target_win.title}[/]")
        with _WINDOW_FOCUS_LOCK:
            _activate_window(hwnd, topmost=topmost, maximize=True)
            time.sleep(0.5)
        return True
    except Exception as e:
        console.print(f"[dim red]   -> Loi khi dua cua so profile len dau: {e}[/]")
        return False


def arrange_window_for_profile_dynamic(page, profile_index, total_profiles=None):
    """
    Layout dong cho 1..N profile. Dinh nghia cuoi file de ghi de logic 4 o cu.
    """
    try:
        browsers = _collect_browser_windows()
        if not browsers:
            logger.error("Loi: Khong bat duoc cua so trinh duyet nao. Bo qua snap.")
            return False

        positions, cell_w, cell_h, cols, rows = _get_screen_grid_positions(total_profiles=total_profiles)
        browsers.sort(key=lambda w: w._hWnd)

        for i, win in enumerate(browsers[:len(positions)]):
            try:
                _maximize_window(win._hWnd)
            except Exception as e:
                logger.error(f"Loi khi phong to 1 cua so: {e}")

        logger.info(
            f"Da phong to {min(len(positions), len(browsers))} cua so trinh duyet."
        )

        try:
            page.bring_to_front()
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"Loi tong quat khi sap xep cua so: {e}")
        return False


def arrange_window_for_profile(page, profile_index, total_profiles=None):
    """
    Override layout cu: ho tro so profile dong thay vi co dinh 4 o.
    """
    try:
        browsers = _collect_browser_windows()
        if not browsers:
            logger.error("Loi: Khong bat duoc cua so trinh duyet nao. Bo qua snap.")
            return False

        positions, cell_w, cell_h, cols, rows = _get_screen_grid_positions(total_profiles=total_profiles)
        browsers.sort(key=lambda w: w._hWnd)

        for i, win in enumerate(browsers[:len(positions)]):
            try:
                _maximize_window(win._hWnd)
            except Exception as e:
                logger.error(f"Loi khi phong to 1 cua so: {e}")

        logger.info(
            f"Da phong to {min(len(positions), len(browsers))} cua so trinh duyet."
        )

        try:
            page.bring_to_front()
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"Loi tong quat khi sap xep cua so: {e}")
        return False


def bind_profile_window(profile_id, profile_index=None, total_profiles=None):
    """Ghi nho HWND cua profile ma khong kich hoat cua so do."""
    try:
        target_win = _resolve_profile_window(
            profile_id=profile_id,
            profile_index=profile_index,
            total_profiles=total_profiles,
        )
        if not target_win:
            return False
        _PROFILE_WINDOW_HWND[str(profile_id)] = target_win._hWnd
        return True
    except Exception as e:
        logger.debug(f"Khong the bind cua so cho profile {profile_id}: {e}")
        return False


def _bring_profile_window_to_top_legacy(profile_id=None, profile_index=None, total_profiles=None, topmost=True, maximize=True):
    """Dua dung cua so browser cua profile len tren cung."""
    try:
        target_win = _resolve_profile_window(
            profile_id=profile_id,
            profile_index=profile_index,
            total_profiles=total_profiles,
        )
        if not target_win:
            return False

        hwnd = target_win._hWnd
        if profile_id is not None:
            _PROFILE_WINDOW_HWND[str(profile_id)] = hwnd
        console.print(f"[dim]   ➔ Đang đưa cửa sổ profile lên đầu: {target_win.title}[/]")

        _maximize_window(hwnd)

        win32gui.SetForegroundWindow(hwnd)

        if topmost:
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOPMOST,
                0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
            )

        time.sleep(0.5)
        return True
    except Exception as e:
        console.print(f"[dim red]   ➔ Lỗi khi đưa cửa sổ profile lên đầu: {e}[/]")
        return False


def take_screenshot(page=None, filename="error_screenshot.png"):
    """
    Chụp ảnh màn hình. 
    Ưu tiên dùng playwright page.screenshot(), nếu không có hoặc lỗi thì dùng pyautogui chụp toàn màn hình.
    """
    try:
        save_path = os.path.join(config.BASE_DIR, filename)
        
        # Thử dùng Playwright
        if page:
            try:
                page.screenshot(path=save_path)
                return save_path
            except Exception as e:
                logger.debug(f"Lỗi khi chụp màn hình bằng Playwright: {e}")
            
        # Fallback dùng pyautogui
        import pyautogui
        try:
            # Đảm bảo trình duyệt lên trên cùng trước khi chụp toàn màn hình
            bring_window_to_top(['抖音', 'TikTok', 'Chrome', 'GemLogin', 'Iron'])
            time.sleep(0.5)
            screenshot = pyautogui.screenshot()
            screenshot.save(save_path)
            return save_path
        except Exception as e:
            console.print(f"[dim red]   ➔ Lỗi khi chụp màn hình bằng pyautogui: {e}[/]")
            
    except Exception as e:
        console.print(f"[dim red]   ➔ Lỗi tổng quát khi chụp màn hình: {e}[/]")
    return None

def notify_intervention(reason, page=None):
    """
    Chụp ảnh và gửi thông báo cần can thiệp qua Telegram.
    """
    from services.telegram_service import send_screenshot_notification
    
    # Chụp ảnh
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"intervention_{ts}.png"
    img_path = take_screenshot(page, fname)
    
    if img_path:
        try:
            send_screenshot_notification(img_path, reason)
            return True
        finally:
            try:
                os.remove(img_path)
            except OSError:
                pass
    return False

def arrange_window_for_profile(page, profile_index, total_profiles=None):
    """
    Sắp xếp cửa sổ trình duyệt (của page) vào 1 trong 4 góc màn hình dạng grid.
    profile_index: 0, 1, 2, 3
    Đã được tối ưu cho màn hình 1920x1080 scale 125%.
    """
    try:
        import pygetwindow as gw
        
        # Tìm TẤT CẢ các cửa sổ có chứa các keyword liên quan
        browsers = []
        for win in gw.getAllWindows():
            if win.title:
                t = win.title.lower()
                # Loại trừ IDE, Terminal, hoặc thư mục dự án
                if any(x in t for x in ["visual studio", "cmd.exe", "powershell", "douyin to tiktok", "antigravity", ".py"]):
                    continue
                # Bắt tất cả các cửa sổ của tool
                if any(k in t for k in ["iron", "brower", "tiktok", "douyin", "抖音", "upload", "new tab"]):
                    browsers.append(win)
        
        if not browsers:
            logger.error(f"Lỗi: Không bắt được cửa sổ trình duyệt nào. Bỏ qua snap.")
            return False
            
        # Sắp xếp danh sách window theo HWND để đảm bảo thứ tự luôn cố định
        browsers.sort(key=lambda w: w._hWnd)
            
        # Kích thước màn hình vật lý (Physical Resolution)
        # Bỏ qua việc chia cho scale, vì thư viện pygetwindow/win32 lấy tọa độ vật lý.
        # Nếu chia cho 1.25 tọa độ sẽ bị co cụm về bên trái.
        screen_width = 1920
        screen_height = 1040 # Chừa lại 40px cho Taskbar

        half_w = screen_width // 2
        half_h = screen_height // 2

        positions = [
            (0, 0),                 # góc trái trên
            (half_w, 0),            # góc phải trên
            (0, half_h),            # góc trái dưới
            (half_w, half_h)        # góc phải dưới
        ]
        
        # Đảm bảo index không vượt quá 3
        pos_idx = profile_index % 4
        
        for i, win in enumerate(browsers[:4]):
            try:
                _maximize_window(win._hWnd)
            except Exception as e:
                logger.error(f"Lỗi khi phóng to 1 cửa sổ: {e}")
                
        logger.info(f"Đã phóng to {min(4, len(browsers))} cửa sổ trình duyệt.")
        
        page.bring_to_front()
        return True

    except Exception as e:
        logger.error(f"Lỗi tổng quát khi sắp xếp cửa sổ: {e}")
    return False
