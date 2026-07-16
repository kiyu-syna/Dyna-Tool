import os
import json
import sys

# Phát hiện nếu đang chạy từ file EXE (PyInstaller)
if getattr(sys, 'frozen', False):
    exe_dir = os.path.dirname(sys.executable)
    # Nếu file exe nằm trong thư mục dist, lấy thư mục cha làm BASE_DIR
    if os.path.basename(exe_dir).lower() == "dist":
        BASE_DIR = os.path.dirname(exe_dir)
    else:
        BASE_DIR = exe_dir
else:
    # File này nằm trong core/, nên phải lấy thư mục cha (project root)
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Đường dẫn file cài đặt (đã chuyển vào thư mục config/)
SETTINGS_FILE = os.path.join(BASE_DIR, "config", "settings.json")

def load_settings():
    default_settings = {
        "SAVE_DIRS": {},
        "NUM_TABS": 1,
        "API_URL": "http://127.0.0.1:1010",
        "PAYMENT_API_URL": "http://localhost:8000",
        "PROFILE_ID": "1",
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
        "TELEGRAM_QUIET_HOURS_ENABLED": False,
        "TELEGRAM_QUIET_START": "22:00",
        "TELEGRAM_QUIET_END": "07:00",
        "MAX_CONCURRENT_DOWNLOADS": 2,
        "MAX_CONCURRENT_FFMPEG": 1,
        "MAX_CONCURRENT_UPLOADS": 2,
        "MIN_LIKES": 5000,
        "MAX_DURATION": 120,
        "UI_THEME": "light",
        "UI_LANGUAGE": "vi",
    }
    
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return {**default_settings, **json.load(f)}
        except Exception as e:
            print(f" Lỗi đọc settings.json: {e}")
            return default_settings
    return default_settings

# Tải dữ liệu
settings = load_settings()

# CẤU HÌNH HỆ THỐNG (ĐƯỢC LOAD TỪ SETTINGS.JSON)
API_URL      = settings.get("API_URL", "http://127.0.0.1:1010")
PROFILE_ID   = settings.get("PROFILE_ID", "1")
PROFILE_IDS  = settings.get("PROFILE_IDS", [PROFILE_ID])
NUM_TABS     = settings.get("NUM_TABS", 1)
TARGET_TAGS  = settings.get("TARGET_TAGS", [])
MAX_NEW_VIDEOS = settings.get("MAX_NEW_VIDEOS", 25)

# --- CẤU HÌNH TELEGRAM ---
TELEGRAM_BOT_TOKEN = settings.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = settings.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_QUIET_HOURS_ENABLED = bool(settings.get("TELEGRAM_QUIET_HOURS_ENABLED", False))
TELEGRAM_QUIET_START = str(settings.get("TELEGRAM_QUIET_START", "22:00"))
TELEGRAM_QUIET_END = str(settings.get("TELEGRAM_QUIET_END", "07:00"))

# --- CẤU HÌNH THƯ MỤC LƯU ---
SAVE_DIR = settings.get("SAVE_DIR", "")

# --- CẤU HÌNH LỌC VIDEO ---
MIN_LIKES = settings.get("MIN_LIKES", 5000)
MAX_DURATION = settings.get("MAX_DURATION", 120)

GOOGLE_SHEET_URL = settings.get("GOOGLE_SHEET_URL", "")

# HÀM LẤY CẤU HÌNH RIÊNG CHO TỪNG PROFILE
def get_profile_settings(profile_id):
    """
    Trả về dict cấu hình đầy đủ cho một profile.
    Kế thừa cấu hình Global, ghi đè bằng cài đặt riêng của profile nếu có.
    """
    # Cấu hình mặc định (Global)
    base = {
        "MIN_LIKES":      MIN_LIKES,
        "MAX_DURATION":   MAX_DURATION,
        "MAX_NEW_VIDEOS": MAX_NEW_VIDEOS,
        "SAVE_DIR":       SAVE_DIR,
        "GOOGLE_SHEET_URL": GOOGLE_SHEET_URL,
    }
    # Đọc cấu hình riêng cho profile này (nếu có)
    profiles_cfg = settings.get("PROFILES", {})
    profile_cfg  = profiles_cfg.get(str(profile_id), {})
    # Ghi đè các key có trong profile config
    base.update(profile_cfg)
    return base


def load_profile_configs():
    """
    Tải tất cả các file cấu hình profile dạng JSON từ thư mục profiles/
    """
    profiles_dir = os.path.join(BASE_DIR, "profile_automation", "profiles")
    if not os.path.exists(profiles_dir):
        os.makedirs(profiles_dir, exist_ok=True)
        return {}

    profiles = {}
    for filename in os.listdir(profiles_dir):
        if filename.endswith(".json") and filename != "profile_example.json":
            filepath = os.path.join(profiles_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    p_id = str(config.get("id"))
                    profiles[p_id] = config
            except Exception as e:
                print(f" Lỗi đọc profile config {filename}: {e}")
    return profiles
