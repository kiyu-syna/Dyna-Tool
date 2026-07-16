import json
import os
import sys
import threading
import time
from datetime import datetime

import psutil
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import core.config as config
import core.shared_state as shared_state
from core.shared_state import DOUYIN_CONTROL
from services import busy_mode_service
from services.gemlogin_browser_service import classify_automation_error
from services.sheets_service import connect_and_style_sheets
import services.stats_service as stats_service

bot = None
_listener_started = False
_handlers_registered = False
_listener_lock = threading.Lock()

MAPPING_FILE = os.path.join(config.BASE_DIR, "telegram_mapping.json")
CAPTION_REQUESTS_FILE = os.path.join(config.BASE_DIR, "telegram_caption_requests.json")
CAPTION_MESSAGE_MAP_FILE = os.path.join(config.BASE_DIR, "telegram_caption_message_map.json")
DEFAULT_CAPTION_TIMEOUT_SEC = 10 * 60
CAPTION_REQUEST_RETENTION_DAYS = 7
PENDING_CAPTION_RETENTION_DAYS = 2
MAX_CAPTION_REQUESTS = 500
MAX_CAPTION_MESSAGE_MAPPINGS = 1000
ERROR_NOTIFICATION_DEDUPE_SECONDS = 10 * 60
_ERROR_NOTIFICATION_TIMES: dict[tuple[str, str, str], float] = {}
_ERROR_NOTIFICATION_LOCK = threading.Lock()


def _safe_print(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, 'encoding', None) or 'ascii'
        safe_message = str(message).encode(encoding, errors='replace').decode(encoding)
        print(safe_message)


def _setting_enabled(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _clock_minutes(value: str) -> int | None:
    try:
        hour_text, minute_text = str(value).strip().split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError):
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour * 60 + minute


def is_telegram_quiet_hours_active(now: datetime | None = None) -> bool:
    if not _setting_enabled(getattr(config, "TELEGRAM_QUIET_HOURS_ENABLED", False)):
        return False

    start = _clock_minutes(getattr(config, "TELEGRAM_QUIET_START", "22:00"))
    end = _clock_minutes(getattr(config, "TELEGRAM_QUIET_END", "07:00"))
    if start is None or end is None:
        return False

    current_time = now or datetime.now()
    current = current_time.hour * 60 + current_time.minute
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _load_json_file(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def _save_json_file(path, data):
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, path)


def _prune_caption_requests(data, now_timestamp=None):
    if not isinstance(data, dict):
        return {}
    now_value = float(now_timestamp if now_timestamp is not None else time.time())
    terminal_cutoff = now_value - CAPTION_REQUEST_RETENTION_DAYS * 86400
    pending_cutoff = now_value - PENDING_CAPTION_RETENTION_DAYS * 86400
    retained = {}

    def timestamp_value(value, fallback):
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(fallback)

    for key, request in data.items():
        if not isinstance(request, dict):
            continue
        status = str(request.get("status") or "")
        updated_at = timestamp_value(
            request.get("updated_at") or request.get("created_at"),
            now_value,
        )
        if status == "pending" and updated_at < pending_cutoff:
            continue
        if status != "pending" and updated_at < terminal_cutoff:
            continue
        retained[str(key)] = request
    if len(retained) <= MAX_CAPTION_REQUESTS:
        return retained
    ordered = sorted(
        retained.items(),
        key=lambda item: timestamp_value(
            item[1].get("updated_at") or item[1].get("created_at"),
            0,
        ),
        reverse=True,
    )
    pending = [(key, value) for key, value in ordered if value.get("status") == "pending"]
    terminal = [(key, value) for key, value in ordered if value.get("status") != "pending"]
    selected = (pending + terminal)[:MAX_CAPTION_REQUESTS]
    return {key: value for key, value in selected}


def _prune_caption_message_map(data, valid_request_keys):
    if not isinstance(data, dict):
        return {}
    valid_keys = set(valid_request_keys)
    retained = [
        (str(message_id), request_key)
        for message_id, request_key in data.items()
        if request_key in valid_keys
    ]
    retained.sort(key=lambda item: int(item[0]) if item[0].isdigit() else 0, reverse=True)
    return dict(retained[:MAX_CAPTION_MESSAGE_MAPPINGS])


def load_mapping():
    return _load_json_file(MAPPING_FILE, {})


def save_mapping(mapping):
    if len(mapping) > 100:
        sorted_keys = sorted(mapping.keys(), key=lambda x: int(x))
        keys_to_keep = sorted_keys[-100:]
        mapping = {k: mapping[k] for k in keys_to_keep}
    _save_json_file(MAPPING_FILE, mapping)


def load_caption_requests():
    return _load_json_file(CAPTION_REQUESTS_FILE, {})


def save_caption_requests(data):
    _save_json_file(CAPTION_REQUESTS_FILE, _prune_caption_requests(data))


def load_caption_message_map():
    return _load_json_file(CAPTION_MESSAGE_MAP_FILE, {})


def save_caption_message_map(data):
    requests = _prune_caption_requests(load_caption_requests())
    _save_json_file(
        CAPTION_MESSAGE_MAP_FILE,
        _prune_caption_message_map(data, requests.keys()),
    )


def prune_telegram_caption_state() -> dict:
    requests_before = load_caption_requests()
    requests_after = _prune_caption_requests(requests_before)
    message_map_before = load_caption_message_map()
    message_map_after = _prune_caption_message_map(
        message_map_before,
        requests_after.keys(),
    )
    _save_json_file(CAPTION_REQUESTS_FILE, requests_after)
    _save_json_file(CAPTION_MESSAGE_MAP_FILE, message_map_after)
    return {
        "caption_requests": len(requests_before) - len(requests_after),
        "caption_message_mappings": len(message_map_before) - len(message_map_after),
    }


def _caption_request_key(profile_id: str, aweme_id: str) -> str:
    return f"{profile_id}:{aweme_id}"


def _ensure_bot():
    global bot
    if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        return None
    if bot is None:
        bot = telebot.TeleBot(config.TELEGRAM_BOT_TOKEN)
        _register_handlers()
    return bot


def _register_handlers():
    global _handlers_registered
    if _handlers_registered or bot is None:
        return
    bot.register_callback_query_handler(handle_callback, func=lambda call: True)
    bot.register_message_handler(cmd_scan_fyp, commands=['scan_fyp'])
    bot.register_message_handler(cmd_scan_follow, commands=['scan_follow'])
    bot.register_message_handler(cmd_stopscan, commands=['stopscan'])
    bot.register_message_handler(cmd_logs, commands=['logs'])
    bot.register_message_handler(cmd_status, commands=['status'])
    bot.register_message_handler(cmd_stats, commands=['stats'])
    bot.register_message_handler(cmd_busy, commands=['busy'])
    bot.register_message_handler(cmd_free, commands=['free'])
    bot.register_message_handler(handle_caption_reply, func=lambda message: True, content_types=['text'])
    _handlers_registered = True


def _pid_from_lock(lock_path):
    if not os.path.exists(lock_path):
        return None
    try:
        with open(lock_path, 'r', encoding='utf-8') as f:
            pid = int(f.read().strip())
        if psutil.pid_exists(pid):
            return pid
    except Exception:
        return None
    return None


def _external_listener_running():
    return _pid_from_lock(os.path.join(config.BASE_DIR, 'bot.lock'))


def ensure_telegram_listener_running():
    global _listener_started
    tg_bot = _ensure_bot()
    if tg_bot is None:
        return
    with _listener_lock:
        if _listener_started:
            return
        if _external_listener_running():
            return
        threading.Thread(target=start_telegram_bot, daemon=True).start()
        _listener_started = True


def send_video_notification(hashtag, video_link, description):
    if is_telegram_quiet_hours_active():
        return
    tg_bot = _ensure_bot()
    if tg_bot is None:
        return
    msg_text = (
        f"VIDEO MỚI ĐÃ LƯU\n\n"
        f"Hashtag: #{hashtag}\n"
        f"Link: {video_link}\n\n"
        f"Mô tả AI:\n{description}"
    )
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("Dùng mô tả dự phòng", callback_data="fallback"),
        InlineKeyboardButton("Xóa video", callback_data="delete"),
    )
    try:
        sent_msg = tg_bot.send_message(
            config.TELEGRAM_CHAT_ID,
            msg_text,
            disable_web_page_preview=True,
            reply_markup=markup,
        )
        mapping = load_mapping()
        mapping[str(sent_msg.message_id)] = video_link
        save_mapping(mapping)
    except Exception as e:
        _safe_print(f"Lỗi gửi Telegram: {e}")


def _default_caption(default_caption: str, video) -> str:
    return str(default_caption or "").strip() or (getattr(video, 'desc', '') or '')


def request_caption_for_video(
    profile_id: str,
    video,
    default_caption: str = "",
    timeout_sec: int = DEFAULT_CAPTION_TIMEOUT_SEC,
    profile_name: str = "",
    source_label: str = "",
    platforms=None,
) -> str | None:
    fallback_caption = _default_caption(default_caption, video)
    if busy_mode_service.is_busy_mode_enabled():
        _safe_print(
            f"[Telegram] Chế độ Bận đang bật, dùng mô tả mặc định cho "
            f"profile {profile_id}, video {getattr(video, 'aweme_id', '')}."
        )
        return fallback_caption
    if is_telegram_quiet_hours_active():
        _safe_print(
            f"[Telegram] Đang trong giờ im lặng, dùng mô tả mặc định cho "
            f"profile {profile_id}, video {getattr(video, 'aweme_id', '')}."
        )
        return fallback_caption

    tg_bot = _ensure_bot()
    if tg_bot is None:
        _safe_print(
            f"[Telegram] Bot chưa sẵn sàng, dùng mô tả mặc định cho "
            f"profile {profile_id}, video {getattr(video, 'aweme_id', '')}."
        )
        return fallback_caption

    ensure_telegram_listener_running()

    key = _caption_request_key(str(profile_id), str(video.aweme_id))
    now = time.time()
    requests = load_caption_requests()
    requests[key] = {
        'profile_id': str(profile_id),
        'aweme_id': str(video.aweme_id),
        'share_url': getattr(video, 'share_url', ''),
        'original_desc': getattr(video, 'desc', '') or '',
        'default_caption': fallback_caption,
        'status': 'pending',
        'action': None,
        'caption': None,
        'created_at': now,
        'updated_at': now,
        'telegram_message_id': None,
    }
    save_caption_requests(requests)

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton('Dùng mô tả mặc định', callback_data='cap_default'),
        InlineKeyboardButton('Dùng mô tả gốc', callback_data='cap_original'),
    )
    markup.row(InlineKeyboardButton('Hủy bỏ video', callback_data='cap_cancel'))

    msg_text = (
        f"VIDEO MỚI CẦN MÔ TẢ\n\n"
        f"Profile: {_profile_display(profile_id, profile_name)}\n"
        f"Nguồn: {source_label or 'Không xác định'}\n"
        f"Aweme ID: {video.aweme_id}\n"
        f"Sẽ đăng lên: {_platform_plan_text(platforms)}\n"
        f"Link: {video.share_url}\n\n"
        f"Mô tả gốc Douyin:\n{video.desc}\n\n"
        f"Mô tả mặc định:\n{fallback_caption or '(đang để trống)'}\n\n"
        "Hãy trả lời trực tiếp tin nhắn này để gửi mô tả mới. "
        "Nếu không phản hồi trong 10 phút, hệ thống sẽ tự dùng mô tả mặc định."
    )

    try:
        sent_msg = tg_bot.send_message(
            config.TELEGRAM_CHAT_ID,
            msg_text,
            disable_web_page_preview=True,
            reply_markup=markup,
        )
        _safe_print(
            f"[Telegram] Đã gửi yêu cầu mô tả. "
            f"profile={profile_id}, aweme_id={video.aweme_id}, message_id={sent_msg.message_id}"
        )
        requests = load_caption_requests()
        if key in requests:
            requests[key]['telegram_message_id'] = sent_msg.message_id
            requests[key]['updated_at'] = time.time()
            save_caption_requests(requests)
        message_map = load_caption_message_map()
        message_map[str(sent_msg.message_id)] = key
        save_caption_message_map(message_map)
    except Exception as e:
        _safe_print(
            f"[Telegram] Gửi yêu cầu mô tả thất bại. "
            f"profile={profile_id}, aweme_id={getattr(video, 'aweme_id', '')}, error={e}"
        )
        return fallback_caption

    started_at = time.time()
    while True:
        requests = load_caption_requests()
        request = requests.get(key)
        if not request:
            return fallback_caption
        if request.get('status') == 'cancelled':
            return None
        if request.get('status') == 'resolved':
            caption = request.get('caption')
            return caption if caption is not None else fallback_caption
        if busy_mode_service.is_busy_mode_enabled():
            busy_caption = request.get('default_caption') or fallback_caption
            requests[key]['status'] = 'resolved'
            requests[key]['action'] = 'busy_mode'
            requests[key]['caption'] = busy_caption
            requests[key]['updated_at'] = time.time()
            save_caption_requests(requests)
            return busy_caption
        if is_telegram_quiet_hours_active():
            quiet_caption = request.get('default_caption') or fallback_caption
            requests[key]['status'] = 'resolved'
            requests[key]['action'] = 'default_quiet_hours'
            requests[key]['caption'] = quiet_caption
            requests[key]['updated_at'] = time.time()
            save_caption_requests(requests)
            return quiet_caption
        if time.time() - started_at >= timeout_sec:
            timeout_caption = request.get('default_caption') or fallback_caption
            requests[key]['status'] = 'resolved'
            requests[key]['action'] = 'default_timeout'
            requests[key]['caption'] = timeout_caption
            requests[key]['updated_at'] = time.time()
            save_caption_requests(requests)
            try:
                message_id = request.get('telegram_message_id')
                if message_id:
                    tg_bot.edit_message_reply_markup(
                        config.TELEGRAM_CHAT_ID,
                        message_id,
                        reply_markup=None,
                    )
                if not is_telegram_quiet_hours_active():
                    tg_bot.send_message(
                        config.TELEGRAM_CHAT_ID,
                        f"Đã hết 10 phút chờ phản hồi. Hệ thống dùng mô tả mặc định "
                        f"cho video {video.aweme_id}.",
                    )
            except Exception:
                pass
            return timeout_caption
        time.sleep(1)


def _resolve_caption_request(message_id: str, caption: str):
    message_map = load_caption_message_map()
    key = message_map.get(str(message_id))
    if not key:
        return False
    requests = load_caption_requests()
    request = requests.get(key)
    if not request or request.get('status') != 'pending':
        return False
    request['status'] = 'resolved'
    request['action'] = 'custom_caption'
    request['caption'] = caption
    request['updated_at'] = time.time()
    requests[key] = request
    save_caption_requests(requests)
    return True


def _resolve_latest_pending_caption_request(caption: str):
    requests = load_caption_requests()
    pending_items = [
        (key, req) for key, req in requests.items()
        if req.get('status') == 'pending'
    ]
    if not pending_items:
        return False

    pending_items.sort(key=lambda item: item[1].get('created_at', 0), reverse=True)
    key, request = pending_items[0]
    request['status'] = 'resolved'
    request['action'] = 'custom_caption'
    request['caption'] = caption
    request['updated_at'] = time.time()
    requests[key] = request
    save_caption_requests(requests)
    return True


def handle_caption_reply(message):
    if not getattr(message, 'text', None):
        return
    reply_to = getattr(message, 'reply_to_message', None)
    text = message.text.strip()
    resolved = False
    if reply_to:
        resolved = _resolve_caption_request(reply_to.message_id, text)
    if not resolved:
        resolved = _resolve_latest_pending_caption_request(text)
    if resolved:
        try:
            bot.reply_to(message, 'Đã nhận mô tả. Hệ thống sẽ dùng nội dung này để đăng video.')
        except Exception:
            pass


def handle_callback(call):
    if call.data.startswith("job_retry|") or call.data.startswith("job_cancel|"):
        action, separator, payload = call.data.partition("|")
        try:
            profile_id, video_id = payload.split("|", 1)
        except ValueError:
            bot.answer_callback_query(call.id, "Dữ liệu video không hợp lệ.")
            return

        from profile_automation.pipeline.video_job_store import VideoJobStore

        store = VideoJobStore()
        if action == "job_retry":
            job = store.retry_job(profile_id, video_id)
            message = (
                f"Đã đưa video {video_id} vào hàng đợi thử lại."
                if job
                else "Không thể thử lại: video đang được xử lý hoặc không còn tồn tại."
            )
        else:
            job = store.cancel_job(
                profile_id,
                video_id,
                reason="Người dùng hủy video từ Telegram.",
            )
            message = (
                f"Đã hủy video {video_id}."
                if job
                else "Không thể hủy: video đang được xử lý hoặc không còn tồn tại."
            )
        bot.answer_callback_query(call.id, message)
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=None,
            )
            bot.send_message(call.message.chat.id, message)
        except Exception:
            pass
        return

    caption_actions = {'cap_default', 'cap_original', 'cap_cancel'}
    if call.data in caption_actions or call.data.startswith('cap_orig|'):
        msg_id = str(call.message.message_id)
        message_map = load_caption_message_map()
        key = message_map.get(msg_id)
        requests = load_caption_requests()
        request = requests.get(key) if key else None
        if not request:
            bot.answer_callback_query(call.id, 'Không tìm thấy yêu cầu mô tả này.')
            return
        if request.get('status') != 'pending':
            bot.answer_callback_query(call.id, 'Video này đã được xử lý trước đó.')
            return

        if call.data == 'cap_cancel':
            request['status'] = 'cancelled'
            request['action'] = 'cancel'
            request['caption'] = None
            answer_text = 'Đã hủy bỏ video.'
            notification_text = f"Đã hủy, video {request.get('aweme_id', '')} sẽ không được đăng."
        elif call.data == 'cap_default':
            request['status'] = 'resolved'
            request['action'] = 'default_button'
            request['caption'] = request.get('default_caption', '')
            answer_text = 'Đã chọn mô tả mặc định.'
            notification_text = f"Đã chọn mô tả mặc định cho video {request.get('aweme_id', '')}."
        else:
            request['status'] = 'resolved'
            request['action'] = 'original_button'
            request['caption'] = request.get('original_desc', '')
            answer_text = 'Đã chọn mô tả gốc.'
            notification_text = f"Đã chọn mô tả gốc cho video {request.get('aweme_id', '')}."

        request['updated_at'] = time.time()
        requests[key] = request
        save_caption_requests(requests)
        bot.answer_callback_query(call.id, answer_text)
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=None,
            )
            bot.send_message(call.message.chat.id, notification_text)
        except Exception:
            pass
        return

    mapping = load_mapping()
    msg_id = str(call.message.message_id)
    if msg_id not in mapping:
        bot.answer_callback_query(call.id, 'Lỗi: Không tìm thấy dữ liệu video này.')
        return

    video_link = mapping[msg_id]
    try:
        sheet = connect_and_style_sheets()
        if not sheet:
            bot.answer_callback_query(call.id, 'Lỗi kết nối Google Sheet.')
            return
        cell = sheet.find(video_link)
        if not cell:
            bot.answer_callback_query(call.id, 'Không tìm thấy video trong Sheet.')
            return
        if call.data == 'delete':
            sheet.delete_rows(cell.row)
            bot.answer_callback_query(call.id, 'Đã xóa video khỏi Sheet.')
        elif call.data == 'fallback':
            sheet.update_cell(cell.row, 7, '??????')
            bot.answer_callback_query(call.id, 'Đã đổi sang mô tả dự phòng.')
    except Exception as e:
        bot.answer_callback_query(call.id, f'Lỗi: {e}')


def send_upload_success_notification(video_id, hashtag):
    if is_telegram_quiet_hours_active():
        return
    tg_bot = _ensure_bot()
    if tg_bot is None:
        return
    msg_text = (
        f"ĐĂNG VIDEO THÀNH CÔNG!\n\n"
        f"ID: {video_id}\n"
        f"Hashtag: #{hashtag}\n"
        f"Thời gian: {__import__('datetime').datetime.now().strftime('%H:%M:%S %d/%m/%Y')}"
    )
    try:
        tg_bot.send_message(config.TELEGRAM_CHAT_ID, msg_text)
    except Exception as e:
        _safe_print(f"Lỗi gửi thông báo upload Telegram: {e}")


_PLATFORM_DISPLAY_NAMES = {
    "tiktok": "TikTok",
    "facebook": "Facebook Reels",
    "youtube": "YouTube Shorts",
}


def _platform_display_name(platform: str) -> str:
    return _PLATFORM_DISPLAY_NAMES.get(str(platform), str(platform).title())


def _telegram_timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S %d/%m/%Y")


def _short_telegram_text(value: object, limit: int = 500) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip() + "..."


def _profile_display(profile_id: str, profile_name: str = "") -> str:
    name = str(profile_name or "").strip()
    return f"{profile_id} - {name}" if name else str(profile_id)


def _platform_plan_text(platforms) -> str:
    names = [_platform_display_name(platform) for platform in platforms or ()]
    return ", ".join(names) if names else "Không có nền tảng nào"


def send_new_video_notification(
    profile_id: str,
    profile_name: str,
    source_label: str,
    video,
    platforms,
) -> None:
    """Notify once when a newly detected video enters the processing queue."""
    if is_telegram_quiet_hours_active():
        return
    tg_bot = _ensure_bot()
    if tg_bot is None:
        return

    author = str(getattr(video, "author_nickname", "") or "").strip() or "Không xác định"
    description = _short_telegram_text(getattr(video, "desc", ""), limit=600)
    msg_text = (
        "PHÁT HIỆN VIDEO DOUYIN MỚI\n\n"
        f"Profile: {_profile_display(profile_id, profile_name)}\n"
        f"Nguồn: {source_label or 'Không xác định'}\n"
        f"Aweme ID: {getattr(video, 'aweme_id', '')}\n"
        f"Tác giả: {author}\n"
        f"Thời lượng: {float(getattr(video, 'duration_seconds', 0) or 0):.1f} giây\n"
        f"Lượt thích: {int(getattr(video, 'like_count', 0) or 0):,}\n"
        f"Sẽ đăng lên: {_platform_plan_text(platforms)}\n"
        "Trạng thái: Đã đưa vào hàng đợi xử lý\n\n"
        f"Mô tả gốc:\n{description or '(trống)'}\n\n"
        f"Link: {getattr(video, 'share_url', '')}\n"
        f"Thời gian: {_telegram_timestamp()}"
    )
    try:
        tg_bot.send_message(
            config.TELEGRAM_CHAT_ID,
            msg_text,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        _safe_print(f"Lỗi gửi thông báo video mới Telegram: {exc}")


def send_video_upload_summary_notification(
    profile_id: str,
    profile_name: str,
    source_label: str,
    video,
    platforms,
    results: dict,
    platform_states: dict | None = None,
) -> None:
    """Send one readable outcome summary after all enabled uploaders have run."""
    if is_telegram_quiet_hours_active():
        return
    tg_bot = _ensure_bot()
    if tg_bot is None:
        return

    platform_states = platform_states or {}
    outcome_lines = []
    success_count = 0
    skipped_count = 0
    failed_count = 0
    for platform in platforms or ():
        display_name = _platform_display_name(platform)
        state = platform_states.get(platform, {}) or {}
        stored_status = str(state.get("status") or "")
        result = results.get(platform)
        reason = _short_telegram_text(state.get("last_error"), limit=240)
        if stored_status == "skipped" or result == "skipped":
            skipped_count += 1
            suffix = f" - {reason}" if reason else ""
            outcome_lines.append(f"- {display_name}: Bỏ qua{suffix}")
        elif stored_status == "success" or result is True:
            success_count += 1
            outcome_lines.append(f"- {display_name}: Đăng thành công")
        else:
            failed_count += 1
            suffix = f" - {reason}" if reason else ""
            outcome_lines.append(f"- {display_name}: Thất bại{suffix}")

    if failed_count:
        title = "KẾT QUẢ XỬ LÝ VIDEO - CÓ LỖI"
    elif success_count:
        title = "ĐĂNG VIDEO HOÀN TẤT"
    else:
        title = "VIDEO ĐÃ ĐƯỢC XỬ LÝ"

    msg_text = (
        f"{title}\n\n"
        f"Profile: {_profile_display(profile_id, profile_name)}\n"
        f"Nguồn: {source_label or 'Không xác định'}\n"
        f"Aweme ID: {getattr(video, 'aweme_id', '')}\n"
        f"Link: {getattr(video, 'share_url', '')}\n\n"
        "Kết quả từng nền tảng:\n"
        + ("\n".join(outcome_lines) if outcome_lines else "- Không có nền tảng nào")
        + "\n\n"
        f"Tổng kết: {success_count} thành công, {skipped_count} bỏ qua, {failed_count} thất bại\n"
        f"Thời gian: {_telegram_timestamp()}"
    )
    try:
        tg_bot.send_message(
            config.TELEGRAM_CHAT_ID,
            msg_text,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        _safe_print(f"Lỗi gửi tổng kết upload Telegram: {exc}")


def send_error_notification(
    error_message: str,
    profile_id: str | None = None,
    video_id: str | None = None,
) -> None:
    if is_telegram_quiet_hours_active():
        return
    tg_bot = _ensure_bot()
    if tg_bot is None:
        return

    error_category = classify_automation_error(error_message)
    fingerprint = (
        error_category
        if error_category != "permanent"
        else " ".join(str(error_message or "").lower().split())[:500]
    )
    notification_key = (str(profile_id or ""), str(video_id or ""), fingerprint)
    now = time.monotonic()
    with _ERROR_NOTIFICATION_LOCK:
        expired_before = now - ERROR_NOTIFICATION_DEDUPE_SECONDS
        expired_keys = [
            key for key, sent_at in _ERROR_NOTIFICATION_TIMES.items() if sent_at < expired_before
        ]
        for key in expired_keys:
            _ERROR_NOTIFICATION_TIMES.pop(key, None)
        last_sent_at = _ERROR_NOTIFICATION_TIMES.get(notification_key, 0.0)
        if last_sent_at and now - last_sent_at < ERROR_NOTIFICATION_DEDUPE_SECONDS:
            _safe_print(
                f"[Telegram] Bỏ qua cảnh báo trùng cho Profile {profile_id or '-'}, "
                f"Video {video_id or '-'} ({error_category})."
            )
            return

    details = []
    if profile_id is not None:
        details.append(f"Profile: {profile_id}")
    if video_id is not None:
        details.append(f"Video: {video_id}")
    if profile_id is not None and video_id is not None:
        try:
            from profile_automation.pipeline.video_job_store import VideoJobStore

            job = VideoJobStore().get_job(str(profile_id), str(video_id)) or {}
            source_label = str(job.get("source_label") or "").strip()
            video_data = job.get("video", {}) or {}
            if source_label:
                details.append(f"Nguồn: {source_label}")
            share_url = str(video_data.get("share_url") or "").strip()
            if share_url:
                details.append(f"Link: {share_url}")
            platform_lines = []
            status_labels = {
                "pending": "Chờ",
                "uploading": "Đang đăng",
                "success": "Thành công",
                "skipped": "Bỏ qua",
                "failed": "Lỗi",
                "disabled": "Tắt",
            }
            for platform in job.get("enabled_platforms", []) or []:
                state = (job.get("platforms", {}) or {}).get(platform, {}) or {}
                status = status_labels.get(
                    str(state.get("status") or "pending"),
                    str(state.get("status") or "Chờ"),
                )
                platform_lines.append(f"- {_platform_display_name(platform)}: {status}")
            if platform_lines:
                details.append("Trạng thái nền tảng:\n" + "\n".join(platform_lines))
        except Exception as exc:
            _safe_print(f"Không thể bổ sung chi tiết hàng đợi vào cảnh báo Telegram: {exc}")
    details.append(f"Lỗi: {error_message}")
    details.append(
        f"Thời gian: {__import__('datetime').datetime.now().strftime('%H:%M:%S %d/%m/%Y')}"
    )
    reply_markup = None
    if profile_id is not None and video_id is not None:
        reply_markup = InlineKeyboardMarkup()
        reply_markup.row(
            InlineKeyboardButton("Thử lại", callback_data=f"job_retry|{profile_id}|{video_id}"),
            InlineKeyboardButton("Hủy bỏ video", callback_data=f"job_cancel|{profile_id}|{video_id}"),
        )
    try:
        tg_bot.send_message(
            config.TELEGRAM_CHAT_ID,
            "CÓ LỖI XẢY RA\n\n" + "\n".join(details),
            reply_markup=reply_markup,
        )
        with _ERROR_NOTIFICATION_LOCK:
            _ERROR_NOTIFICATION_TIMES[notification_key] = now
    except Exception as e:
        _safe_print(f"Lỗi gửi cảnh báo Telegram: {e}")


def send_screenshot_notification(image_path, caption):
    if is_telegram_quiet_hours_active():
        return
    tg_bot = _ensure_bot()
    if tg_bot is None:
        return
    try:
        with open(image_path, 'rb') as photo:
            tg_bot.send_photo(config.TELEGRAM_CHAT_ID, photo, caption=f"HỆ THỐNG CẦN CHÚ Ý\n\n{caption}")
    except Exception as e:
        _safe_print(f"Lỗi gửi ảnh chụp Telegram: {e}")


def send_diagnostic_notification(record: dict) -> None:
    if is_telegram_quiet_hours_active():
        return
    tg_bot = _ensure_bot()
    if tg_bot is None:
        return

    last_response = record.get("last_response", {}) or {}
    response_line = ""
    if last_response:
        response_line = (
            f"\nResponse cuối: {last_response.get('status') or '-'} "
            f"{_short_telegram_text(last_response.get('url'), limit=300)}"
        )
    caption = (
        "CHẨN ĐOÁN TỰ ĐỘNG\n\n"
        f"Profile: {record.get('profile_id') or '-'}\n"
        f"Video: {record.get('video_id') or '-'}\n"
        f"Nền tảng: {_platform_display_name(record.get('platform'))}\n"
        f"Thời gian: {record.get('occurred_at') or '-'}\n"
        f"URL: {_short_telegram_text(record.get('url'), limit=350) or '-'}"
        f"{response_line}\n"
        f"Lỗi: {_short_telegram_text(record.get('error'), limit=650)}"
    )
    screenshot_path = str(record.get("screenshot_path") or "")
    metadata_path = str(record.get("metadata_path") or "")
    try:
        if screenshot_path and os.path.isfile(screenshot_path):
            with open(screenshot_path, "rb") as photo:
                tg_bot.send_photo(config.TELEGRAM_CHAT_ID, photo, caption=caption[:1000])
        else:
            tg_bot.send_message(
                config.TELEGRAM_CHAT_ID,
                caption,
                disable_web_page_preview=True,
            )
        if metadata_path and os.path.isfile(metadata_path) and hasattr(tg_bot, "send_document"):
            with open(metadata_path, "rb") as document:
                tg_bot.send_document(
                    config.TELEGRAM_CHAT_ID,
                    document,
                    caption="Dữ liệu chẩn đoán đầy đủ (URL, response cuối và thời điểm lỗi).",
                )
    except Exception as exc:
        _safe_print(f"Lỗi gửi gói chẩn đoán Telegram: {exc}")


def cmd_scan_fyp(message):
    bot.reply_to(message, 'Chức năng quét FYP đã được gỡ. Hãy dùng tab Tracking Douyin.')


def cmd_scan_follow(message):
    bot.reply_to(message, 'Chức năng quét Follow đã được gỡ. Hãy dùng tab Tracking Douyin.')


def cmd_stopscan(message):
    if not DOUYIN_CONTROL['running']:
        bot.reply_to(message, 'Hệ thống không có tiến trình quét nào đang chạy.')
        return
    DOUYIN_CONTROL['running'] = False
    bot.reply_to(message, 'Đã gửi lệnh dừng quét Douyin. Hệ thống sẽ dọn các tab đang mở.')


def cmd_logs(message):
    log_file = os.path.join(config.BASE_DIR, 'logs', 'system.log')
    if not os.path.exists(log_file):
        bot.reply_to(message, 'File log chưa được tạo.')
        return
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            log_text = ''.join(lines[-20:]) if lines else 'File log trống.'
        bot.send_message(message.chat.id, f"20 DÒNG LOG MỚI NHẤT:\n\n{log_text}")
    except Exception as e:
        bot.reply_to(message, f'Lỗi khi đọc log: {e}')


def cmd_status(message):
    busy_label = 'BẬN' if busy_mode_service.is_busy_mode_enabled() else 'RẢNH'
    msg = (
        'TRẠNG THÁI HỆ THỐNG\n\n'
        f'Chế độ caption: {busy_label}\n'
        'Tracking Douyin: dùng trong giao diện Dyna Tool\n'
        'TikTok Scheduler: đang chạy ngầm (theo lịch Sheet)'
    )
    bot.reply_to(message, msg)


def _authorized_control_message(message) -> bool:
    configured_chat_id = str(getattr(config, 'TELEGRAM_CHAT_ID', '') or '').strip()
    message_chat_id = str(getattr(getattr(message, 'chat', None), 'id', '') or '').strip()
    return bool(configured_chat_id and message_chat_id == configured_chat_id)


def cmd_busy(message):
    if not _authorized_control_message(message):
        return
    busy_mode_service.set_busy_mode(True, source='telegram:/busy')
    bot.reply_to(
        message,
        'Đã bật chế độ BẬN. Video mới sẽ dùng mô tả mặc định và không chờ caption Telegram.',
    )


def cmd_free(message):
    if not _authorized_control_message(message):
        return
    busy_mode_service.set_busy_mode(False, source='telegram:/free')
    bot.reply_to(
        message,
        'Đã chuyển sang RẢNH. Video mới sẽ gửi yêu cầu caption Telegram như bình thường.',
    )


def cmd_stats(message):
    today_data = stats_manager.get_stats_today()
    week_data = stats_manager.get_last_7_days_stats()
    total_scanned = sum(d['scanned'] for d in week_data)
    total_uploaded = sum(d['uploaded'] for d in week_data)
    msg = (
        'BÁO CÁO THỐNG KÊ\n\n'
        f"Hôm nay: quét {today_data['scanned']} / đăng {today_data['uploaded']}\n"
        f"7 ngày: quét {total_scanned} / đăng {total_uploaded}"
    )
    bot.reply_to(message, msg)


def start_telegram_bot():
    tg_bot = _ensure_bot()
    if tg_bot is None:
        return
    _safe_print('Telegram Bot listener đang khởi động...')
    while True:
        try:
            tg_bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            _safe_print(f'Telegram Bot lỗi, thử lại sau 5 giây: {e}')
            time.sleep(5)
