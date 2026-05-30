import threading
import time
import os
import sys
import psutil
from utils import logger, console
from telegram_manager import start_telegram_bot
from bot_douyin import run_automation
from upload_tiktok import schedule_upload_loop
import config

def run_bot_system():
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
                    logger.warning(f"⚠️ Bot đã đang chạy ngầm rồi! (PID: {old_pid})")
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

    logger.info("==========================================================")
    logger.info("       HỆ THỐNG TỰ ĐỘNG HÓA DOUYIN -> TIKTOK             ")
    logger.info("==========================================================")
    logger.info("[bold green]✅ Đã khởi tạo hệ thống tổng hợp (Chế độ chạy ngầm sẵn sàng).[/bold green]")
    logger.info("[bold yellow]ℹ️ Hướng dẫn: Sử dụng Telegram để điều khiển:[/bold yellow]")
    logger.info("   - /scan_fyp   : Bắt đầu quét Fyp")
    logger.info("   - /scan_follow: Bắt đầu quét Follow")
    logger.info("   - /stopscan   : Dừng quét Douyin")
    logger.info("   - /status     : Kiểm tra trạng thái hệ thống")
    logger.info("   - /logs       : Xem 20 dòng log mới nhất")
    logger.info("[bold cyan]⏰ Upload tự động theo lịch: Điền giờ vào cột 'LỊCH ĐĂNG' trong Google Sheet.[/bold cyan]")
    logger.info("----------------------------------------------------------")

    # 1. Khởi chạy duy nhất 1 luồng Telegram Bot
    threading.Thread(target=start_telegram_bot, daemon=True).start()
    
    # 2. Khởi chạy luồng Scheduler Upload (tự động đăng theo lịch trong Google Sheet)
    threading.Thread(target=schedule_upload_loop, daemon=True).start()
    
    # 3. Douyin bot chỉ chạy khi có lệnh /scan từ Telegram

    # Giữ cho script chính không bị thoát
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n[bold red]🛑 Đang đóng hệ thống...[/bold red]")
    finally:
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                logger.info("Đã xóa lock file.")
            except Exception as e:
                logger.error(f"Không thể xóa lock file {lock_file}: {e}")

if __name__ == "__main__":
    import sys

    if "--bot" in sys.argv:
        run_bot_system()
    else:
        from login_window import launch_app
        launch_app()