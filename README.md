# Dyna Tool

Dyna Tool là ứng dụng desktop hỗ trợ tự động hóa tải, xử lý và đăng video lên nhiều nền tảng. Dự án gồm giao diện Electron/React, backend Python và payment server dùng FastAPI.

## Tính năng chính

- Giao diện desktop Electron + React.
- Tải và xử lý video dọc/YouTube Shorts.
- Tự động hóa Douyin/TikTok theo profile.
- Hỗ trợ upload lên TikTok, YouTube và Facebook.
- Tích hợp Telegram và theo dõi trạng thái tác vụ.
- Payment API tích hợp SePay và MongoDB.
- Trợ lý AI trong desktop, dùng khóa Gemini/Groq/OpenRouter được bảo vệ trên máy chủ Dyna.

## Cấu trúc dự án

```text
Dyna Tool/
├── main.py                 # Điểm khởi động ứng dụng
├── core/                   # Cấu hình và tiện ích dùng chung
├── services/               # Dịch vụ theo miền: account, browser, publishing...
├── profile_automation/     # Pipeline, trình duyệt, watcher và uploader theo profile
├── desktop/                # Electron + React frontend
├── desktop_backend/        # API backend cho ứng dụng desktop
├── payment_server/         # FastAPI payment API
├── extensions/             # Browser extensions
├── image/                  # Icon và tài nguyên hình ảnh
├── runtime/                # State, log và file tạm (không commit)
└── tests/                  # Chỉ test tích hợp xuyên nhiều miền
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

### Tùy chọn: Xử lý video AI

Tab **VẬN HÀNH → Xử lý video AI** dùng `faster-whisper` để tạo phụ đề cục bộ.
FFmpeg cần có trong `PATH` hoặc tại `C:\ffmpeg\bin`. Cài runtime nhận dạng bằng:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-video-ai.txt
```

Model nhận dạng được tải ở lần chạy đầu tiên và lưu trong thư mục dữ liệu runtime
của Dyna. Nếu chưa cài runtime này, các chức năng chỉnh sửa phụ đề, làm mờ và
chèn phụ đề bằng FFmpeg vẫn có thể sử dụng.

## Cấu hình bí mật

Không commit token, mật khẩu hoặc file `.env` lên Git. Tạo file cấu hình local từ template:

```powershell
Copy-Item payment_server\.env.example payment_server\.env
```

Sau đó mở `payment_server\.env` và điền các giá trị cần thiết như MongoDB, SePay, thông tin ngân hàng và tài khoản admin.

### Cấu hình Dyna AI trên máy chủ

Điền một hoặc nhiều khóa cho từng nhà cung cấp trong `payment_server\.env` (nhiều khóa ngăn cách bằng dấu phẩy hoặc dấu chấm phẩy):

```dotenv
AI_PROVIDER_ORDER=gemini,groq,openrouter
AI_GEMINI_API_KEYS=gemini_key_1,gemini_key_2
AI_GROQ_API_KEYS=groq_key_1,groq_key_2
AI_OPENROUTER_API_KEYS=openrouter_key_1,openrouter_key_2
```

Nếu chỉ có một khóa, có thể dùng các biến ngắn `AI_GEMINI_API_KEY`,
`AI_GROQ_API_KEY` và `AI_OPENROUTER_API_KEY`. Gateway sẽ thử lần lượt theo
`AI_PROVIDER_ORDER`; khi một key bị 401/403/404, hết quota/credit, bị giới hạn
tốc độ hoặc gặp lỗi mạng/5xx, key đó được tạm ngưng và request chuyển sang key
hoặc provider tiếp theo. Thời gian tạm ngưng được tính theo `Retry-After` của
nhà cung cấp (nếu có), nên server không cần khởi động lại để xoay key.
Riêng khi thêm, xóa hoặc đổi key trong `.env`, hãy khởi động lại Payment Server
để nạp cấu hình mới.

Không đặt các khóa này trong `settings.json`, mã React/Electron hoặc app desktop. Máy chủ tự xoay khóa và nhà cung cấp khi gặp hết hạn mức, giới hạn tốc độ, lỗi mạng hoặc lỗi tạm thời. `AI_REQUESTS_PER_MINUTE` giới hạn chi phí theo từng tài khoản Dyna; `AI_REQUIRE_ACTIVE_LICENSE=true` chỉ cho tài khoản Premium còn hiệu lực sử dụng.

Các file local khác như `auth.json`, `settings.json`, `state.json`, log và trạng thái runtime cũng được bỏ qua bởi `.gitignore`. Sao chép `config/settings.example.json` thành `config/settings.json` khi cần tùy chỉnh cấu hình chạy local.

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
