import time
from core.utils import logger
from profile_automation.pipeline.profile_manager import ProfileManager
from profile_automation.pipeline.profile_worker import ProfileWorker
from profile_automation.douyin_sources import get_douyin_sources
from services.telegram_service import send_error_notification

def start_profile_scheduler_loop(stop_event=None):
    """
    Vòng lặp chính quản lý việc quét và đăng bài tự động theo Profile.
    """
    logger.info("[bold green]⏰ Hệ thống Profile-Based Scheduler đã khởi động![/bold green]")
    
    next_run = {}  # {(profile_id, source_key): timestamp_seconds}
    
    while True:
        if stop_event and stop_event.is_set():
            logger.info("Scheduler dừng theo yêu cầu.")
            break
            
        try:
            active_profiles = ProfileManager.get_active_profiles()
            
            if not active_profiles:
                logger.info("Không phát hiện profile hoạt động nào trong thư mục profiles/.")
                time.sleep(30)
                continue
                
            now = time.time()
            
            for profile in active_profiles:
                p_id = str(profile.get("id"))
                sources = get_douyin_sources(profile)
                if not sources:
                    continue

                worker = ProfileWorker(profile)
                active_keys = {(p_id, source["source_key"]) for source in sources}
                next_run = {
                    key: value
                    for key, value in next_run.items()
                    if key[0] != p_id or key in active_keys
                }
                for index, source in enumerate(sources):
                    interval_secs = source["check_interval_minutes"] * 60
                    run_key = (p_id, source["source_key"])
                    next_run.setdefault(run_key, now + (index * 5))
                    if now < next_run[run_key]:
                        continue

                    source_label = source.get("target_display_name") or f"...{source['target_sec_uid'][-10:]}"
                    logger.info(
                        f"⏳ Kích hoạt quét Profile {profile.get('name')} (ID: {p_id}), "
                        f"nguồn {source_label}"
                    )
                    next_run[run_key] = time.time() + interval_secs

                    try:
                        worker.run_source(source)
                    except Exception as e:
                        logger.error(
                            f"Lỗi khi chạy worker Profile {p_id}, nguồn {source_label}: {e}"
                        )
                        send_error_notification(
                            f"Lỗi worker tại nguồn {source_label}: {e}",
                            profile_id=p_id,
                        )
                        
            # Nghỉ 10 giây trước khi check tiếp
            time.sleep(10)
            
        except Exception as e:
            logger.error(f"Lỗi vòng lặp Scheduler chính: {e}")
            send_error_notification(f"Lỗi vòng lặp Scheduler chính: {e}")
            time.sleep(30)
