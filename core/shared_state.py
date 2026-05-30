import os
import json
import sys
import threading

# Determine base path regardless of whether we're bundled as exe or running from source
if getattr(sys, 'frozen', False):
    exe_dir = os.path.dirname(sys.executable)
    if os.path.basename(exe_dir).lower() == "dist":
        base_path = os.path.dirname(exe_dir)
    else:
        base_path = exe_dir
else:
    base_path = os.path.dirname(os.path.abspath(__file__))

STATE_FILE = os.path.join(base_path, "state.json")

class ControlState:
    def __init__(self):
        # RLock tránh deadlock khi code bên ngoài đã giữ lock
        # rồi lại gọi __getitem__/__setitem__ vốn cũng cần lock.
        self._lock = threading.RLock()
        if not os.path.exists(STATE_FILE):
            self._save({"running": False, "mode": 0})

    def _load(self):
        with self._lock:
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                return {"running": False, "mode": 0}
            except Exception:
                return {"running": False, "mode": 0}

    def _save(self, data):
        with self._lock:
            try:
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)
            except Exception:
                pass

    def __getitem__(self, key):
        return self._load().get(key, False)

    def __setitem__(self, key, value):
        data = self._load()
        data[key] = value
        self._save(data)

# Trạng thái dùng chung giữa các process thông qua file json
DOUYIN_CONTROL = ControlState()
