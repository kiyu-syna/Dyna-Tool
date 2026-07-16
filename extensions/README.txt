Dyna - tiện ích trình duyệt Douyin
=================================

Thư mục extension:
  extensions/douyin_downloader_9.0.58

Quy trình sử dụng:
1. Mở Dyna Desktop. Bridge nội bộ sẽ chạy tại 127.0.0.1:8765.
2. Mở chrome://extensions trong trình duyệt chính dùng để lướt Douyin.
3. Bật Developer mode (Chế độ dành cho nhà phát triển).
4. Tắt hoặc xóa bản cũ của downloader để tránh chạy trùng hai extension.
5. Chọn Load unpacked (Tải tiện ích đã giải nén) và chọn thư mục phía trên.
6. Mở hoặc tải lại trang video Douyin.
7. Nút tải gốc màu xanh trên video sẽ đổi thành nút Dyna chữ "D".
8. Nhấn nút "D", chọn Profile, rồi nhấn "Tải và đăng".

Nút Dyna gọi trực tiếp getMediaList() của nút tải gốc, giữ nguyên cách extension
ghép đúng awemeInfo của từng ô video và chọn chất lượng cao nhất. Chrome tải
video bằng phiên đăng nhập của trình duyệt chính. Khi tải hoàn tất,
extension đọc đường dẫn tuyệt đối từ chrome.downloads rồi gửi đường dẫn và
metadata cho Dyna. Dyna sao chép file vào thư mục quản lý, kiểm tra video và đưa
vào luồng caption, giới hạn tài nguyên, đăng nền tảng bằng GemLogin Profile đã
chọn. File gốc trong thư mục Downloads của Chrome vẫn được giữ nguyên.

Sau mỗi lần sửa mã extension, nhấn Reload tại chrome://extensions và tải lại các
tab Douyin đang mở.
