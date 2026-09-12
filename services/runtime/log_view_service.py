from __future__ import annotations

import hashlib
import logging
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from application.tracking.profile_management_service import ProfileManagementService


STRUCTURED_LINE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
    r"\s*\|\s*(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*\|\s*(?P<message>.*)$"
)
PROFILE_PATTERNS = (
    re.compile(r"\[Profile\s+(\d+)\]", re.IGNORECASE),
    re.compile(r"\bProfile\s+(\d+)\b", re.IGNORECASE),
)


def _rotated_log_paths(log_file: Path) -> list[Path]:
    rotated = []
    for index in range(20, 0, -1):
        candidate = Path(f"{log_file}.{index}")
        if candidate.is_file():
            rotated.append(candidate)
    if log_file.is_file():
        rotated.append(log_file)
    return rotated


def _tail_lines(log_file: Path, limit: int) -> tuple[list[str], int]:
    rows: deque[str] = deque(maxlen=max(20, int(limit)))
    files = _rotated_log_paths(log_file)
    for path in files:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                rows.extend(line.rstrip("\r\n") for line in handle)
        except OSError:
            continue
    return list(rows), len(files)


def _cursor(path: Path, offset: int) -> str:
    try:
        stat = path.stat()
        safe_offset = max(0, min(int(offset), stat.st_size))
        with path.open("rb") as handle:
            handle.seek(max(0, safe_offset - 64))
            signature = hashlib.sha1(handle.read(min(64, safe_offset))).hexdigest()[:12]
    except OSError:
        return ""
    return f"{stat.st_dev}:{stat.st_ino}:{safe_offset}:{signature}"


def _incremental_lines(log_file: Path, cursor: str, limit: int) -> tuple[list[str], str] | None:
    """Read only complete lines appended after a cursor; return None after rotation/truncation."""
    try:
        device, inode, raw_offset, signature = cursor.split(":", 3)
        offset = int(raw_offset)
        stat = log_file.stat()
        if str(stat.st_dev) != device or str(stat.st_ino) != inode or not 0 <= offset <= stat.st_size:
            return None
        if _cursor(log_file, offset).rsplit(":", 1)[-1] != signature:
            return None
        with log_file.open("rb") as handle:
            handle.seek(offset)
            payload = handle.read()
        complete_size = len(payload)
        if payload and not payload.endswith((b"\n", b"\r")):
            newline = max(payload.rfind(b"\n"), payload.rfind(b"\r"))
            complete_size = newline + 1 if newline >= 0 else 0
        complete = payload[:complete_size].decode("utf-8", errors="replace")
        lines = complete.splitlines()
        return lines[-max(20, int(limit)):], _cursor(log_file, offset + complete_size)
    except (OSError, TypeError, ValueError):
        return None


def _profile_id(message: str) -> str:
    for pattern in PROFILE_PATTERNS:
        match = pattern.search(message)
        if match:
            return str(match.group(1))
    return ""


def _legacy_level(message: str) -> str:
    normalized = message.casefold()
    if any(marker in normalized for marker in ("traceback", "error", "failed", "lỗi", "thất bại")):
        return "ERROR"
    if any(marker in normalized for marker in ("warning", "cảnh báo", "không thể", "bỏ qua")):
        return "WARNING"
    return "INFO"


def localize_log_message(message: str) -> str:
    """Việt hóa các thông báo cũ trước khi hiển thị trên cửa sổ nhật ký."""
    text = str(message or "")
    workload_match = re.fullmatch(
        r"\[Workload\] Profile (\S+) video (\S+) queued for (\S+) \(priority=(\d+)\)\.",
        text,
    )
    if workload_match:
        profile_id, video_id, resource, priority = workload_match.groups()
        resource_labels = {
            "upload": "đăng video",
            "download": "tải video",
            "ffmpeg": "chuyển đổi video",
        }
        return (
            f"[Điều phối] Profile {profile_id}, video {video_id} đang chờ tài nguyên "
            f"{resource_labels.get(resource, resource)} (ưu tiên={priority})."
        )

    vietnamese_workload = re.fullmatch(
        r"\[Điều phối\] Profile (\S+), video (\S+) đang chờ tài nguyên (\S+) \(ưu tiên=(\d+)\)\.",
        text,
    )
    if vietnamese_workload:
        profile_id, video_id, resource, priority = vietnamese_workload.groups()
        resource_labels = {
            "upload": "đăng video",
            "download": "tải video",
            "ffmpeg": "chuyển đổi video",
        }
        return (
            f"[Điều phối] Profile {profile_id}, video {video_id} đang chờ tài nguyên "
            f"{resource_labels.get(resource, resource)} (ưu tiên={priority})."
        )

    replacements = (
        ("[Upload Pipeline]", "[Tiến trình đăng]"),
        ("[Upload pipeline]", "[Tiến trình đăng]"),
        ("[Local Upload]", "[Đăng video nội bộ]"),
        ("[Extension]", "[Tiện ích]"),
        ("[Publish Center]", "[Trung tâm đăng]"),
        ("[Ready Check]", "[Kiểm tra sẵn sàng]"),
        ("[TEST UPLOAD]", "[ĐĂNG THỬ]"),
        ("[INTERCEPT ERROR]", "[LỖI BẮT GÓI]"),
        ("[INTERCEPT", "[BẮT GÓI"),
        ("[License]", "[Bản quyền]"),
        ("[Maintenance]", "[Bảo trì]"),
        ("[Diagnostic]", "[Chẩn đoán]"),
        ("[Desktop Runtime]", "[Tiến trình theo dõi]"),
        ("Đã hoàn tất xoay dữ liệu runtime", "Đã hoàn tất dọn dữ liệu vận hành"),
        ("Runtime data rotation completed", "Đã hoàn tất dọn dữ liệu vận hành"),
        ("Could not send diagnostic artifact to Telegram", "Không thể gửi dữ liệu chẩn đoán lên Telegram"),
        ("Could not read Profile", "Không thể đọc Profile"),
        ("Could not resume video", "Không thể khôi phục video"),
        ("Could not process video", "Không thể xử lý video"),
        ("Received video", "Đã nhận video"),
        ("YouTube checks completed with no issues", "YouTube đã kiểm tra xong, không phát hiện vấn đề"),
        ("YouTube check status", "Trạng thái kiểm tra YouTube"),
        ("Opening YouTube upload page", "Đang mở trang đăng YouTube"),
        ("Selected YouTube video through background Playwright upload", "Đã chọn video YouTube ở chế độ nền"),
        ("Clicked Next", "Đã nhấn Tiếp"),
        ("YouTube Shorts upload completed", "Đăng YouTube Shorts hoàn tất"),
        ("YouTube Shorts upload failed", "Đăng YouTube Shorts thất bại"),
        ("YouTube Shorts conversion failed", "Chuyển đổi YouTube Shorts thất bại"),
        ("Deleted temporary 9:16 video", "Đã xóa video 9:16 tạm"),
        ("Could not delete temporary 9:16 video", "Không thể xóa video 9:16 tạm"),
        ("9:16 video conversion completed", "Chuyển đổi video 9:16 hoàn tất"),
        ("9:16 conversion completed", "Chuyển đổi 9:16 hoàn tất"),
        ("Cannot save license file", "Không thể lưu file bản quyền"),
        ("Error verifying license", "Lỗi xác minh bản quyền"),
        ("Local Chromium launch/connection failed on attempt", "Mở hoặc kết nối Chromium nội bộ thất bại ở lần"),
        ("HTTP Request", "Yêu cầu HTTP"),
        ("YouTube detected copyrighted content", "YouTube phát hiện nội dung có bản quyền"),
        ("because it was published successfully before", "vì đã đăng thành công trước đó"),
        ("Skipped", "Bỏ qua"),
        ("Valid video", "Video hợp lệ"),
        ("is valid:", "hợp lệ:"),
        ("Started tracking", "Bắt đầu theo dõi"),
        ("Sent stop command for", "Đã gửi lệnh dừng"),
        ("stopped because of an error", "đã dừng do lỗi"),
        ("Tracking thread", "Luồng theo dõi"),
        ("has ended", "đã kết thúc"),
        ("scanning source", "đang quét nguồn"),
        ("Closed browser for", "Đã đóng trình duyệt cho"),
        ("Could not read browser settings for", "Không đọc được cấu hình trình duyệt của"),
        ("Could not close browser for", "Không thể đóng trình duyệt cho"),
        ("Could not clean", "Không thể dọn"),
        ("Could not delete", "Không thể xóa"),
        ("Maximized", "Đã phóng to"),
        ("browser windows", "cửa sổ trình duyệt"),
        ("General error while arranging windows", "Lỗi tổng quát khi sắp xếp cửa sổ"),
        ("Error maximizing a window", "Lỗi khi phóng to cửa sổ"),
        ("Playwright screenshot error", "Lỗi khi chụp màn hình bằng Playwright"),
        ("Traceback (most recent call last):", "Chi tiết kỹ thuật của lỗi:"),
        ("Target page, context or browser has been closed", "Trang hoặc trình duyệt đã bị đóng"),
        ("BrowserContext.new_cdp_session", "Không thể tạo phiên điều khiển trình duyệt"),
        ("TimeoutError", "Quá thời gian chờ"),
        ("Timeout", "Quá thời gian chờ"),
        ("post click failed", "không thể nhấn nút Đăng"),
        ("=True", "=có"),
        ("=False", "=không"),
    )
    localized = text
    for source, target in replacements:
        localized = localized.replace(source, target)
    localized = re.sub(
        r"https?://\S{180,}",
        "<đường dẫn kỹ thuật đã được ẩn>",
        localized,
    )
    return localized


def _is_hidden_technical_message(message: str) -> bool:
    """Ẩn các dòng chẩn đoán mạng quá dài, không hữu ích trong nhật ký vận hành."""
    normalized = str(message or "").lstrip()
    return normalized.startswith(("[DOUYIN RUNTIME]", "[DOUYIN RESPONSE]"))


def parse_log_entries(lines: list[str], *, id_prefix: str = "") -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    inherited_profile = ""
    inherited_level = "INFO"
    inherited_timestamp = ""
    inside_traceback = False
    for index, raw in enumerate(lines):
        match = STRUCTURED_LINE.match(raw)
        if match:
            inside_traceback = False
            timestamp = match.group("timestamp")
            level = match.group("level")
            message = match.group("message")
            inherited_timestamp = timestamp
            inherited_level = level
        else:
            if raw.startswith("Traceback"):
                inside_traceback = True
                continue
            if inside_traceback:
                continue
            timestamp = ""
            level = _legacy_level(raw)
            message = raw
            if raw.startswith(("Traceback", "  File ", "    ", "playwright.")):
                timestamp = inherited_timestamp
                level = inherited_level

        if _is_hidden_technical_message(message):
            continue

        profile_id = _profile_id(message)
        if profile_id:
            inherited_profile = profile_id
        elif message.startswith(("Traceback", "  File ", "    ", "playwright.")):
            profile_id = inherited_profile

        entries.append(
            {
                "id": f"{id_prefix}{index}:{timestamp}:{profile_id}:{level}",
                "timestamp": timestamp.replace(" ", "T") if timestamp else "",
                "time": timestamp[-8:] if timestamp else "",
                "level": level,
                "profile_id": profile_id,
                "message": localize_log_message(message),
                "raw": raw,
                "legacy": match is None,
            }
        )
    return entries


def _profile_rows(profiles: ProfileManagementService | None) -> list[dict[str, str]]:
    if profiles is None or not profiles.profile_dir.is_dir():
        return []
    rows: list[dict[str, str]] = []
    for path in sorted(profiles.profile_dir.glob("profile_*.json")):
        profile_id = path.stem.removeprefix("profile_")
        try:
            profile = profiles.load(profile_id)
        except Exception:
            continue
        rows.append(
            {
                "id": str(profile_id),
                "name": str(profile.get("name") or f"Profile {profile_id}"),
            }
        )
    return rows


def build_log_snapshot(
    log_file: str | Path,
    *,
    limit: int = 2000,
    profiles: ProfileManagementService | None = None,
    cursor: str = "",
) -> dict[str, Any]:
    path = Path(log_file)
    incremental = _incremental_lines(path, cursor, limit) if cursor else None
    reset = bool(cursor) and incremental is None
    if incremental is None:
        lines, file_count = _tail_lines(path, limit)
        try:
            next_cursor = _cursor(path, path.stat().st_size)
        except OSError:
            next_cursor = ""
        id_prefix = f"{next_cursor}:"
    else:
        lines, next_cursor = incremental
        file_count = len(_rotated_log_paths(path))
        id_prefix = f"{cursor}:"
    try:
        modified_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="microseconds")
    except OSError:
        modified_at = ""
    entries = parse_log_entries(lines, id_prefix=id_prefix)
    return {
        "lines": lines,
        "entries": entries,
        "profiles": _profile_rows(profiles) if incremental is None else [],
        "updated_at": modified_at,
        "file_count": file_count,
        "total": len(entries),
        "cursor": next_cursor,
        "reset": reset,
    }


def reset_log_files(log_file: str | Path) -> dict[str, int]:
    """Clear the live log safely and remove its rotated backups."""
    path = Path(log_file).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    loggers = [logging.getLogger()]
    loggers.extend(
        item for item in logging.Logger.manager.loggerDict.values()
        if isinstance(item, logging.Logger)
    )
    handlers = []
    seen_handler_ids: set[int] = set()
    for current_logger in loggers:
        for handler in current_logger.handlers:
            base_filename = getattr(handler, "baseFilename", "")
            if not base_filename or id(handler) in seen_handler_ids:
                continue
            try:
                matches = Path(base_filename).resolve() == path
            except OSError:
                matches = False
            if matches:
                handlers.append(handler)
                seen_handler_ids.add(id(handler))

    handlers.sort(key=id)
    for handler in handlers:
        handler.acquire()
    try:
        cleared = 0
        live_stream_cleared = False
        for handler in handlers:
            stream = getattr(handler, "stream", None)
            if stream is None or getattr(stream, "closed", False):
                continue
            handler.flush()
            stream.seek(0)
            stream.truncate(0)
            stream.flush()
            live_stream_cleared = True
        if not live_stream_cleared:
            path.write_text("", encoding="utf-8")
        cleared += 1

        removed_rotated = 0
        for rotated in _rotated_log_paths(path):
            if rotated == path:
                continue
            try:
                rotated.unlink()
                removed_rotated += 1
            except FileNotFoundError:
                continue
            except OSError:
                rotated.write_text("", encoding="utf-8")
                cleared += 1
        return {"cleared": cleared, "removed_rotated": removed_rotated}
    finally:
        for handler in reversed(handlers):
            handler.release()
