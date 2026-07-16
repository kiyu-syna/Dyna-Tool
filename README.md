# Dyna Tool

Dyna Tool là ứng dụng desktop hỗ trợ tự động hóa tải, xử lý và đăng video lên nhiều nền tảng. Dự án gồm giao diện Electron/React, backend Python và payment server dùng FastAPI.

## Tính năng chính

- Giao diện desktop Electron + React.
- Tải và xử lý video dọc/YouTube Shorts.
- Tự động hóa Douyin/TikTok theo profile.
- Hỗ trợ upload lên TikTok, YouTube và Facebook.
- Tích hợp Telegram, Google Sheets và theo dõi trạng thái tác vụ.
- Payment API tích hợp SePay và MongoDB.

## Cấu trúc dự án

```text
Dyna Tool/
├── main.py                 # Điểm khởi động ứng dụng
├── core/                   # Cấu hình và tiện ích dùng chung
├── services/               # Các dịch vụ nghiệp vụ
├── automation/             # Lập lịch và tiện ích tự động hóa
├── profile_automation/     # Pipeline xử lý theo profile
├── desktop/                # Electron + React frontend
├── desktop_backend/        # API backend cho ứng dụng desktop
├── payment_server/         # FastAPI payment API
├── extensions/             # Browser extensions
├── image/                  # Icon và tài nguyên hình ảnh
└── tests/                  # Automated tests
```

## Yêu cầu môi trường

- Windows 10/11.
- Python 3.11 trở lên.
- Node.js và npm.
- MongoDB nếu sử dụng payment server.
- Các trình duyệt và extension cần thiết cho các tính năng upload/automation.

## Cài đặt

Từ thư mục gốc dự án:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r payment_server\requirements.txt
```

Cài dependencies và build giao diện desktop:

```powershell
cd desktop
npm ci
npm run build
cd ..
```

## Cấu hình bí mật

Không commit token, mật khẩu hoặc file `.env` lên Git. Tạo file cấu hình local từ template:

```powershell
Copy-Item payment_server\.env.example payment_server\.env
```

Sau đó mở `payment_server\.env` và điền các giá trị cần thiết như MongoDB, SePay, thông tin ngân hàng và tài khoản admin.

Các file local khác như `auth.json`, `settings.json`, `state.json`, log và trạng thái runtime cũng được bỏ qua bởi `.gitignore`.

## Chạy dự án

Chạy payment server:

```powershell
payment_server\start_payment_server.cmd
```

Hoặc chạy trực tiếp:

```powershell
cd payment_server
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Build frontend trước khi chạy ứng dụng chính:

```powershell
cd desktop
npm run build
cd ..
python main.py
```

Trong lúc phát triển frontend, có thể dùng:

```powershell
cd desktop
npm run dev
```

Payment API có health check tại `http://localhost:8000/health` và tài liệu API tại `http://localhost:8000/docs`.

## Kiểm thử

Từ thư mục gốc:

```powershell
python -m pytest
```

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
