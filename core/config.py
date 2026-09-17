import os
import json
import sys

_configured_data_dir = os.environ.get("DYNA_DATA_DIR", "").strip()
if _configured_data_dir:
    BASE_DIR = os.path.abspath(_configured_data_dir)
# Phát hiện nếu đang chạy từ file EXE (PyInstaller)
elif getattr(sys, 'frozen', False):
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
        "API_URL": "http://127.0.0.1:1010",
        "MAX_CONCURRENT_DOWNLOADS": 2,
        "MAX_CONCURRENT_FFMPEG": 1,
        "MAX_CONCURRENT_UPLOADS": 2,
        "TELEGRAM_NOTIFICATION_TYPES": {
            "new_video": True,
            "upload_success": True,
            "upload_failure": True,
            "high_ram": True,
            "job_confirmation": True,
        },
        "UI_THEME": "system",
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

API_URL = settings.get("API_URL", "http://127.0.0.1:1010")


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
