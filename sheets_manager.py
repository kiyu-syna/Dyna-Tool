import gspread
from oauth2client.service_account import ServiceAccountCredentials
from utils import console

def _apply_lich_dang_validation(sheet):
    """Thêm date picker (lịch chọn ngày) cho cột LỊCH ĐĂNG (cột J)."""
    try:
        sheet_id = sheet._properties['sheetId']
        body = {
            "requests": [
                {
                    "setDataValidation": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,   # Bỏ qua header (dòng 0)
                            "endRowIndex": 2000,
                            "startColumnIndex": 9,  # Cột J (0-indexed)
                            "endColumnIndex": 10
                        },
                        "rule": {
                            "condition": {
                                "type": "DATE_IS_VALID"
                            },
                            "inputMessage": "⏰ Định dạng: HH:MM hoặc HH:MM DD/MM/YYYY",
                            "showCustomUi": True,
                            "strict": False  # Cho phép nhập tay định dạng khác
                        }
                    }
                }
            ]
        }
        sheet.spreadsheet.batch_update(body)
    except Exception as e:
        console.print(f"[yellow]⚠️ Không thể đặt date picker: {e}[/yellow]")

def _extract_gid(url):
    """Trích xuất tham số gid từ URL Google Sheet."""
    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        # gid thường nằm trong fragment (#gid=...) hoặc query (?gid=...)
        fragment = parsed.fragment  # ví dụ: "gid=735267447"
        if "gid=" in fragment:
            return fragment.split("gid=")[-1].split("&")[0]
        qs = parse_qs(parsed.query)
        if "gid" in qs:
            return qs["gid"][0]
    except:
        pass
    return None

def connect_and_style_sheets(sheet_url=None):
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        import config
        import os
        service_account_path = os.path.join(config.BASE_DIR, "service_account.json")
        creds = ServiceAccountCredentials.from_json_keyfile_name(service_account_path, scope)
        client = gspread.authorize(creds)

        # Ưu tiên sheet_url được truyền vào, nếu không thì dùng Global URL từ config
        import config
        url = sheet_url or config.GOOGLE_SHEET_URL
        if url:
            spreadsheet = client.open_by_url(url)
            # Tìm tab tương ứng với gid nếu có
            gid = _extract_gid(url)
            if gid:
                try:
                    gid_int = int(gid)
                    sheet = next((ws for ws in spreadsheet.worksheets() if ws.id == gid_int), None)
                    if sheet is None:
                        console.print(f"[yellow]⚠️ Không tìm thấy tab gid={gid}, dùng tab đầu tiên.[/yellow]")
                        sheet = spreadsheet.sheet1
                except:
                    sheet = spreadsheet.sheet1
            else:
                sheet = spreadsheet.sheet1
        else:
            sheet = client.open("Douyin Live Link").sheet1
        
        expected_headers = ["HASHTAG", "LINK VIDEO", "THỜI LƯỢNG", "LƯỢT TIM", "THỜI GIAN", "TẢI VỀ", "MÔ TẢ", "TRẠNG THÁI", "POST TIME", "LỊCH ĐĂNG"]
        current_headers = sheet.row_values(1)
        
        # Nếu chưa có header hoặc header cũ không đủ cột, thì cập nhật lại dòng 1
        if not current_headers or current_headers[:5] != expected_headers[:5] or len(current_headers) < len(expected_headers):
            sheet.update('A1:J1', [expected_headers])
            
            # ĐỊNH DẠNG BẢNG CHO ĐẸP
            try:
                # In đậm header và cố định dòng 1
                sheet.format("A1:J1", {"textFormat": {"bold": True}, "horizontalAlignment": "CENTER", "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}})
                sheet.freeze(rows=1)
                
                # Căn chỉnh độ rộng cột (ước lượng)
                # HASHTAG, LINK, DUR, LIKE, TIME, DOWNLOADED, DESC, STATUS, POST_TIME, LICH_DANG
                widths = [100, 300, 80, 80, 100, 80, 250, 100, 150, 150]
                body = {"requests": []}
                for i, w in enumerate(widths):
                    body["requests"].append({
                        "updateDimensionProperties": {
                            "range": {"sheetId": sheet._properties['sheetId'], "dimension": "COLUMNS", "startIndex": i, "endIndex": i+1},
                            "properties": {"pixelSize": w},
                            "fields": "pixelSize"
                        }
                    })
                sheet.spreadsheet.batch_update(body)
            except: pass
        
        # Luôn đảm bảo cột LỊCH ĐĂNG có date picker (ngay cả khi header đã tồn tại trước)
        _apply_lich_dang_validation(sheet)
            
        return sheet
    except Exception as e:
        from utils import logger
        logger.error(f"[red]Lỗi Sheet: {e}[/]")
        return None
