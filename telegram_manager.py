import telebot
import config
import json
import os
import requests
import threading
from sheets_manager import connect_and_style_sheets

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import shared_state
from shared_state import DOUYIN_CONTROL
import stats_manager

# Khởi tạo bot
bot = telebot.TeleBot(config.TELEGRAM_BOT_TOKEN)

MAPPING_FILE = os.path.join(config.BASE_DIR, "telegram_mapping.json")

def load_mapping():
    if os.path.exists(MAPPING_FILE):
        try:
            with open(MAPPING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_mapping(mapping):
    # Tự động dọn dẹp: Chỉ giữ lại 100 bản ghi mới nhất
    if len(mapping) > 100:
        # Lấy danh sách key (message_id) và sắp xếp theo thứ tự (giả định ID tăng dần)
        sorted_keys = sorted(mapping.keys(), key=lambda x: int(x))
        # Chỉ giữ lại 100 key cuối cùng
        keys_to_keep = sorted_keys[-100:]
        mapping = {k: mapping[k] for k in keys_to_keep}
        
    with open(MAPPING_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=4)

def send_video_notification(hashtag, video_link, description):
    """Gửi thông báo video mới lên Telegram với nút bấm"""
    if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        return
    
    msg_text = (
        f"🎬 *VIDEO MỚI ĐÃ LƯU*\n\n"
        f"🏷️ Hashtag: #{hashtag}\n"
        f"🔗 Link: {video_link}\n\n"
        f"📝 *Mô tả AI:*\n{description}\n\n"
        f"💡 _Reply tin nhắn này để sửa mô tả, hoặc dùng nút bấm bên dưới:_"
    )
    
    # Tạo nút bấm
    markup = InlineKeyboardMarkup()
    btn_fallback = InlineKeyboardButton("🔄 desc: 😭💔🥀", callback_data="fallback")
    btn_delete = InlineKeyboardButton("🗑️ Delete", callback_data="delete")
    markup.add(btn_fallback, btn_delete)
    
    try:
        sent_msg = bot.send_message(
            config.TELEGRAM_CHAT_ID, 
            msg_text, 
            parse_mode="Markdown", 
            disable_web_page_preview=True,
            reply_markup=markup
        )
        
        mapping = load_mapping()
        mapping[str(sent_msg.message_id)] = video_link
        save_mapping(mapping)
    except Exception as e:
        print(f"⚠️ Lỗi gửi Telegram: {e}")

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """Xử lý khi người dùng nhấn nút bấm Inline"""
    mapping = load_mapping()
    msg_id = str(call.message.message_id)
    
    if msg_id not in mapping:
        bot.answer_callback_query(call.id, "❌ Lỗi: Không tìm thấy dữ liệu video này.")
        return
    
    video_link = mapping[msg_id]
    
    try:
        sheet = connect_and_style_sheets()
        if not sheet: 
            bot.answer_callback_query(call.id, "❌ Lỗi kết nối Google Sheet.")
            return
            
        cell = sheet.find(video_link)
        if not cell:
            bot.answer_callback_query(call.id, "❌ Không tìm thấy video trong Sheet.")
            return

        if call.data == "delete":
            # Trước khi xóa dòng, lấy hashtag và link để tìm file xóa cho sạch máy
            try:
                row_data = sheet.row_values(cell.row)
                # Giả định cấu trúc cột: HASHTAG(A), LINK(B)
                hashtag = row_data[0]
                video_id = __import__('re').search(r'video/(\d+)', video_link)
                if video_id:
                    video_id = video_id.group(1)
                    # Tìm thư mục lưu từ config.SAVE_DIRS
                    import config
                    target_dir = config.SAVE_DIRS.get(hashtag.lower())
                    if target_dir:
                        file_path = os.path.join(target_dir, f"{video_id}.mp4")
                        if os.path.exists(file_path):
                            os.remove(file_path)
            except: pass

            sheet.delete_rows(cell.row)
            bot.answer_callback_query(call.id, "🗑️ Đã xóa video khỏi Sheet và Máy tính!")
            bot.edit_message_text(
                f"🗑️ *ĐÃ XÓA VIDEO*\nLink: {video_link}", 
                chat_id=call.message.chat.id, 
                message_id=call.message.message_id,
                parse_mode="Markdown"
            )
        elif call.data == "fallback":
            sheet.update_cell(cell.row, 7, "😭💔🥀")
            bot.answer_callback_query(call.id, "✅ Đã đổi mô tả thành 😭💔🥀")
            # Thông báo bằng tin nhắn mới để chắc chắn người dùng thấy
            bot.send_message(call.message.chat.id, f"✅ Đã cập nhật mô tả video {video_link} thành 😭💔🥀")

    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Lỗi: {e}")

def send_upload_success_notification(video_id, hashtag):
    """Gửi thông báo khi đăng video thành công"""
    if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        return
    
    msg_text = (
        f"✅ *ĐĂNG VIDEO THÀNH CÔNG!*\n\n"
        f"🆔 ID: `{video_id}`\n"
        f"🏷️ Hashtag: #{hashtag}\n"
        f"⏱ Thời gian: {__import__('datetime').datetime.now().strftime('%H:%M:%S %d/%m/%Y')}\n\n"
        f"💡 _Scheduler đang chạy ngầm và sẽ tự động đăng._"
    )
    
    try:
        bot.send_message(config.TELEGRAM_CHAT_ID, msg_text, parse_mode="Markdown")
    except Exception as e:
        print(f"⚠️ Lỗi gửi thông báo Upload: {e}")

def send_screenshot_notification(image_path, caption):
    """Gửi ảnh chụp màn hình kèm thông báo lỗi"""
    if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        return
    
    try:
        with open(image_path, 'rb') as photo:
            bot.send_photo(
                config.TELEGRAM_CHAT_ID, 
                photo, 
                caption=f"🚨 *HỆ THỐNG CẦN CHÚ Ý*\n\n{caption}", 
                parse_mode="Markdown"
            )
    except Exception as e:
        print(f"⚠️ Lỗi gửi ảnh Telegram: {e}")

@bot.message_handler(commands=['scan_fyp'])
def cmd_scan_fyp(message):
    """Bắt đầu quét Douyin chế độ Fyp"""
    if DOUYIN_CONTROL["running"]:
        bot.reply_to(message, "⚠️ Hệ thống đang trong quá trình quét Douyin rồi!")
        return

    DOUYIN_CONTROL["mode"] = 0
    DOUYIN_CONTROL["running"] = True
    bot.reply_to(message, "🔍 *Bắt đầu quét Fyp theo hashtag...*\nĐã nhận lệnh, đang mở trình duyệt.", parse_mode="Markdown")
    
    # Kích hoạt luồng tự động hóa
    import bot_douyin
    threading.Thread(target=bot_douyin.run_automation, daemon=True).start()

@bot.message_handler(commands=['scan_follow'])
def cmd_scan_follow(message):
    """Bắt đầu quét Douyin chế độ Follow"""
    if DOUYIN_CONTROL["running"]:
        bot.reply_to(message, "⚠️ Hệ thống đang trong quá trình quét Douyin rồi!")
        return

    DOUYIN_CONTROL["mode"] = 1
    DOUYIN_CONTROL["running"] = True
    bot.reply_to(message, "🔍 *Bắt đầu quét kênh Follow...*\nĐã nhận lệnh, đang mở trình duyệt.", parse_mode="Markdown")
    
    # Kích hoạt luồng tự động hóa
    import bot_douyin
    threading.Thread(target=bot_douyin.run_automation, daemon=True).start()

@bot.message_handler(commands=['stopscan'])
def cmd_stopscan(message):
    """Dừng quét Douyin"""
    if not DOUYIN_CONTROL["running"]:
        bot.reply_to(message, "⚠️ Hệ thống không có tiến trình quét nào đang chạy.")
        return

    DOUYIN_CONTROL["running"] = False
    bot.reply_to(message, "🛑 *Đã gửi lệnh dừng quét Douyin.*\\nHệ thống sẽ dọn dẹp các tab đang mở.", parse_mode="Markdown")

@bot.message_handler(commands=['logs'])
def cmd_logs(message):
    """Lấy 20 dòng log mới nhất"""
    log_file = os.path.join(config.BASE_DIR, "system.log")
    if not os.path.exists(log_file):
        bot.reply_to(message, "❌ File log chưa được tạo.")
        return
    
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            log_text = "".join(lines[-20:]) if lines else "File log trống."
            
        bot.send_message(
            message.chat.id, 
            f"📝 *20 DÒNG LOG MỚI NHẤT:*\\n\\n```\\n{log_text}\\n```", 
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Lỗi khi đọc log: {e}")

@bot.message_handler(commands=['status'])
def cmd_status(message):
    """Kiểm tra trạng thái hệ thống"""
    dy_status = "🔴 Đã dừng"
    if DOUYIN_CONTROL["running"]:
        mode_str = "Fyp" if DOUYIN_CONTROL["mode"] == 0 else "Follow"
        dy_status = f"🟢 Đang quét ({mode_str})"
    
    msg = (
        f"📊 *TRẠNG THÁI HỆ THỐNG*\\n\\n"
        f"🤖 Douyin Bot: {dy_status}\\n"
        f"⏱ TikTok Scheduler: 🟢 Đang chạy ngầm (theo lịch Sheet)\\n\\n"
        f"💡 Lệnh quét:\\n"
        f"   - /scan_fyp: Quét trang đề xuất\\n"
        f"   - /scan_follow: Quét kênh Follow\\n"
        f"   - /stopscan: Dừng quét\\n\\n"
        f"📋 Dùng /logs để xem chi tiết nhật ký."
    )
    bot.reply_to(message, msg, parse_mode="Markdown")

@bot.message_handler(commands=['stats'])
def cmd_stats(message):
    """Báo cáo thống kê tình hình trong ngày và trong tuần"""
    today_data = stats_manager.get_stats_today()
    week_data = stats_manager.get_last_7_days_stats()
    
    # Tính tổng tuần
    total_scanned = sum(d['scanned'] for d in week_data)
    total_uploaded = sum(d['uploaded'] for d in week_data)
    
    msg = (
        f"📊 *BÁO CÁO THỐNG KÊ (STATS)*\n\n"
        f"📅 *Hôm nay:* \n"
        f"  - Đã quét: `{today_data['scanned']}` video\n"
        f"  - Đã đăng: `{today_data['uploaded']}` video\n\n"
        f"🗓️ *Tổng cộng 7 ngày qua:* \n"
        f"  - Đã quét: `{total_scanned}`\n"
        f"  - Đã đăng: `{total_uploaded}`\n\n"
        f"🚀 _Tiếp tục phát triển nhé!_"
    )
    bot.reply_to(message, msg, parse_mode="Markdown")

def start_telegram_bot():
    """Hàm chạy listener để lắng nghe reply (nên chạy trong thread riêng)"""
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_BOT_TOKEN != "YOUR_BOT_TOKEN_HERE":
        print(f"🤖 Telegram Bot listener đang khởi động...")
        while True:
            try:
                bot.infinity_polling(timeout=60, long_polling_timeout=60)
            except Exception as e:
                print(f"⚠️ Lỗi Bot Telegram (Đang thử lại sau 5s): {e}")
                time.sleep(5)
