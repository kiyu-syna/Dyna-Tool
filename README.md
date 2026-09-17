# Dyna Tool

Dyna Tool là ứng dụng desktop hỗ trợ tự động hóa tải, xử lý và đăng video lên nhiều nền tảng. Dự án gồm giao diện Tauri/React và backend Python. Electron vẫn được giữ làm phương án dự phòng.

## Tính năng chính

- Giao diện desktop Tauri + React.
- Tải và xử lý video dọc/YouTube Shorts.
- Tự động hóa Douyin/TikTok theo profile.
- Hỗ trợ upload lên TikTok, YouTube và Facebook.
- Tích hợp Telegram và theo dõi trạng thái tác vụ.

## Cấu trúc dự án

```text
Dyna Tool/
├── main.py                 # Điểm khởi động ứng dụng
├── core/                   # Cấu hình và tiện ích dùng chung
├── services/               # Dịch vụ theo miền: browser, publishing...
├── profile_automation/     # Pipeline, trình duyệt, watcher và uploader theo profile
├── desktop/                # Tauri + React frontend (có Electron dự phòng)
├── desktop_backend/        # API backend cho ứng dụng desktop
├── extensions/             # Browser extensions
├── image/                  # Icon và tài nguyên hình ảnh
├── runtime/                # State, log và file tạm (không commit)
└── tests/                  # Chỉ test tích hợp xuyên nhiều miền
```

## Yêu cầu môi trường

- Windows 10/11.
- Python 3.11 trở lên.
- Node.js và npm.
- Các trình duyệt và extension cần thiết cho các tính năng upload/automation.

## Cài đặt

Từ thư mục gốc dự án:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Cài dependencies và build giao diện desktop:

```powershell
cd desktop
npm ci
npm run build
cd ..
```

## Cấu hình

Sao chép `config/settings.example.json` thành `config/settings.json` khi cần tùy chỉnh cấu hình chạy local. Không đưa token dịch vụ bên thứ ba vào mã nguồn hoặc giao diện desktop.

## Chạy dự án

Chạy ứng dụng desktop Tauri khi phát triển:

```powershell
cd desktop
npm run dev
```

`npm run dev`, `npm start` và `npm run dev:tauri` đều chạy Tauri. Không cần build EXE để dùng chế độ này.
Ở chế độ dev, script tự khởi động dịch vụ Telegram cục bộ và tự tắt dịch vụ khi
đóng Tauri; không cần chạy server riêng.

Có thể mở mã Tauri hiện tại từ thư mục gốc bằng:

```powershell
.\.venv\Scripts\python.exe main.py
```

Electron chưa bị xóa. Khi cần chạy bản dự phòng, dùng `npm run dev:electron`. Script đóng gói Electron cũ nằm tại `npm run package:electron`; script đóng gói Tauri là `npm run package:win` / `npm run package:tauri`. `python main.py` chạy `npm run dev`, vì vậy không mở nhầm binary release cũ.

## Kiểm thử

Từ thư mục gốc:

```powershell
python -m unittest discover -s . -p "test_*.py" -q
```

Test được đặt gần module tương ứng; chỉ các bài test xuyên nhiều miền mới nằm trong
`tests/integration`.

## Quy trình Git cơ bản

```powershell
git status
git add <file-hoặc-thư-mục>
git commit -m "Mô tả thay đổi"
git pull --rebase origin main
git push origin HEAD:main
```

Trước khi commit, luôn kiểm tra các file sẽ được đưa vào commit:

```powershell
git diff --cached --name-status
git diff --cached --stat
```

## Lưu ý bảo mật

Nếu một secret đã từng được commit hoặc push lên GitHub, việc thêm file vào `.gitignore` không xóa secret khỏi lịch sử Git. Hãy thu hồi/đổi secret đó ngay và làm sạch lịch sử repository nếu cần.

## Thành phần mã nguồn mở

Tính năng lồng tiếng tiếng Việt offline sử dụng
[VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS) của Phạm Nguyễn Ngọc Bảo,
được phát hành theo giấy phép Apache-2.0. Model được tải về máy ở lần sử dụng
đầu tiên và chạy bằng ONNX Runtime trên CPU.
