import threading
import time
import os
import sys
import subprocess

sys.dont_write_bytecode = True

import psutil
from core.utils import logger, console
from services.telegram_service import ensure_telegram_listener_running, start_telegram_bot
import core.config as config


def launch_desktop_app() -> int:
    """Launch the built Electron frontend; Electron owns the Python API process."""
    desktop_dir = os.path.join(config.BASE_DIR, "desktop")
    electron_exe = os.path.join(
        desktop_dir,
        "node_modules",
        "electron",
        "dist",
        "electron.exe",
    )
    entry_html = os.path.join(desktop_dir, "dist", "index.html")
    if not os.path.exists(electron_exe):
        raise RuntimeError(
            "Chưa cài Electron. Hãy chạy 'npm install' trong thư mục desktop."
        )
    if not os.path.exists(entry_html):
        raise RuntimeError(
            "Frontend Electron chưa được build. Hãy chạy 'npm run build' trong thư mục desktop."
        )
    process = subprocess.Popen([electron_exe, desktop_dir], cwd=desktop_dir)
    try:
        return int(process.wait() or 0)
    except KeyboardInterrupt:
        process.terminate()
        return int(process.wait() or 0)

# =========================================
# # main.py = file bootstrap của toàn project
# #
# # File này quyết định app sẽ chạy ở mode nào:
# # - mode GUI bình thường
# # - mode bot nền cũ
# # - mode bot nền theo profile
# #
# # Nó không chứa logic UI chi tiết hay logic upload cụ thể.
# # Vai trò chính của nó là nối các hệ thống lớn lại với nhau.
# =========================================


def start_primary_background_services():
    """Start shared services that must have exactly one owner process."""
    listener_thread = threading.Thread(
        target=start_telegram_bot,
        daemon=True,
        name="telegram-listener",
    )
    listener_thread.start()
    return [listener_thread]


def start_profile_automation_services():
    """Start only the profile pipeline; reuse the primary Telegram listener."""
    ensure_telegram_listener_running()
    from profile_automation.pipeline.profile_scheduler import start_profile_scheduler_loop

    scheduler_thread = threading.Thread(
        target=start_profile_scheduler_loop,
        daemon=True,
        name="profile-scheduler",
    )
    scheduler_thread.start()
    return [scheduler_thread]

def run_bot_system():
    # # lock_file dùng để chặn việc mở nhiều process bot nền cùng lúc.
    # # Nếu không khóa bằng PID, nhiều bot có thể chạy chồng nhau và gây lỗi khó debug.
    lock_file = os.path.join(config.BASE_DIR, "bot.lock")
    current_pid = os.getpid()

    if os.path.exists(lock_file):
        try:
            with open(lock_file, "r") as f:
                old_pid = int(f.read().strip())

            if psutil.pid_exists(old_pid):
                proc = psutil.Process(old_pid)
                p_name = proc.name().lower()
                # Kiểm tra nếu tiến trình cũ vẫn là python hoặc dynatool và không phải tiến trình hiện tại
                if (("python" in p_name or "dynatool" in p_name) and old_pid != current_pid):
                    logger.warning(f" Bot đã đang chạy ngầm rồi! (PID: {old_pid})")
                    sys.exit()
            else:
                logger.info(f"Cũ PID {old_pid} không tồn tại. Ghi đè lock file.")
        except (ValueError, FileNotFoundError, psutil.NoSuchProcess) as e:
            logger.warning(f"Lỗi đọc hoặc xử lý lock file: {e}. Sẽ tạo lock file mới.")
        except Exception as e:
            logger.error(f"Lỗi không xác định khi kiểm tra lock file: {e}. Sẽ tạo lock file mới.")

    # Ghi PID mới vào file lock
    # # Nếu đi được tới đây thì process hiện tại được phép trở thành bot đang chạy chính thức.
    # # Ghi PID hiện tại vào file lock chính là hành động "chiếm quyền chạy nền".
    try:
        with open(lock_file, "w") as f:
            f.write(str(current_pid))
    except Exception as e:
        logger.error(f"Không thể ghi PID vào lock file {lock_file}: {e}")
        sys.exit()

    logger.info("  ====  DYNA TOOL ====   ")

    # Process nền mặc định chỉ sở hữu các dịch vụ dùng chung. Tracking Douyin
    # được khởi động thủ công từ giao diện và luôn đi qua pipeline theo profile.
    start_primary_background_services()

    # Giữ cho script chính không bị thoát
    # # Tracking Douyin kiểu mới theo profile không khởi động từ hàm này.
    # # Luồng đó được kích hoạt từ giao diện Tracking Douyin / Profile Bot.
    # #
    # # Giữ process chính sống.
    # # Các thread ở trên là daemon thread, nên nếu hàm main thoát thì chúng cũng tắt theo.
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n[bold red] Đang đóng hệ thống...[/bold red]")
    finally:
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                logger.info("Đã xóa lock file.")
            except Exception as e:
                logger.error(f"Không thể xóa lock file {lock_file}: {e}")

def run_profile_bot_system():
    # # Đây là mode chạy mới hơn.
    # # Nó điều khiển pipeline automation theo từng profile trong profile_automation/.
    lock_file = os.path.join(config.BASE_DIR, "profile_bot.lock")
    current_pid = os.getpid()

    if os.path.exists(lock_file):
        try:
            with open(lock_file, "r") as f:
                old_pid = int(f.read().strip())

            if psutil.pid_exists(old_pid):
                proc = psutil.Process(old_pid)
                p_name = proc.name().lower()
                if (("python" in p_name or "dynatool" in p_name) and old_pid != current_pid):
                    logger.warning(f"⚠️ Profile Bot đã đang chạy ngầm rồi! (PID: {old_pid})")
                    sys.exit()
            else:
                logger.info(f"Cũ PID {old_pid} không tồn tại. Ghi đè lock file.")
        except (ValueError, FileNotFoundError, psutil.NoSuchProcess) as e:
            logger.warning(f"Lỗi đọc hoặc xử lý lock file: {e}. Sẽ tạo lock file mới.")
        except Exception as e:
            logger.error(f"Lỗi không xác định khi kiểm tra lock file: {e}. Sẽ tạo lock file mới.")

    # Ghi PID mới vào file lock
    try:
        with open(lock_file, "w") as f:
            f.write(str(current_pid))
    except Exception as e:
        logger.error(f"Không thể ghi PID vào lock file {lock_file}: {e}")
        sys.exit()

    logger.info("   ====  DYNA TOOL ====   ")

    start_profile_automation_services()

    # Giữ cho script chính không bị thoát
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n[bold red] Đang đóng hệ thống Profile Bot...[/bold red]")
    finally:
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                logger.info("Đã xóa profile lock file.")
            except Exception as e:
                logger.error(f"Không thể xóa lock file {lock_file}: {e}")


if __name__ == "__main__":
    # # Bộ chuyển mode runtime:
    # # --profile-bot : chạy backend mới theo profile
    # # --bot         : chạy backend cũ tổng quát
    # # không có flag : mở Electron + React
    import sys

    if "--profile-bot" in sys.argv:
        run_profile_bot_system()
    elif "--bot" in sys.argv:
        run_bot_system()
    else:
        raise SystemExit(launch_desktop_app())
