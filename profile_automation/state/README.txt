# Thư mục này lưu trạng thái runtime riêng cho từng nguồn Douyin của Profile.
# Các file được tự động tạo khi chạy lần đầu.
# Format mới: profile_{id}_source_{source_key}_seen.json
# File profile_{id}_seen.json là dữ liệu cũ và được tự động migrate cho nguồn cũ.
#
# Không cần tạo file thủ công.
# Không commit thư mục này vào git (đã có trong .gitignore).
#
# video_jobs.json lưu hàng đợi xử lý bền vững cho từng video:
# caption đã chọn, đường dẫn file tải, lỗi cuối và trạng thái TikTok/Facebook/YouTube.
# File này giúp tiếp tục đúng bước sau khi Dyna Tool được khởi động lại.
