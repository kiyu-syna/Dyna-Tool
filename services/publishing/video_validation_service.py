from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class VideoValidationError(RuntimeError):
    pass


class FFprobeNotFoundError(VideoValidationError):
    pass


class InvalidVideoFileError(VideoValidationError):
    pass


def resolve_ffprobe() -> str:
    for candidate in (shutil.which("ffprobe"), "C:/ffmpeg/bin/ffprobe.exe"):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise FFprobeNotFoundError(
        "Không tìm thấy ffprobe trong PATH hoặc tại C:/ffmpeg/bin/ffprobe.exe."
    )


def validate_video_file(
    video_path: str,
    *,
    require_audio: bool = True,
    timeout_seconds: int = 60,
) -> dict:
    source = Path(video_path).expanduser().resolve()
    if not source.is_file():
        raise InvalidVideoFileError(f"Không tìm thấy file video: {source}")
    file_size = source.stat().st_size
    if file_size < 1024:
        raise InvalidVideoFileError(f"File video quá nhỏ ({file_size} byte): {source}")

    command = [
        resolve_ffprobe(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(source),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=max(1, int(timeout_seconds)),
        )
    except subprocess.TimeoutExpired as exc:
        raise InvalidVideoFileError(f"ffprobe bị quá thời gian khi đọc file: {source}") from exc
    except OSError as exc:
        raise FFprobeNotFoundError(f"Không thể chạy ffprobe: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or "ffprobe không đọc được file").strip()
        raise InvalidVideoFileError(f"File video không hợp lệ: {detail}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise InvalidVideoFileError("ffprobe trả về dữ liệu JSON không hợp lệ.") from exc

    streams = payload.get("streams") or []
    format_info = payload.get("format") or {}
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        None,
    )
    if not video_stream:
        raise InvalidVideoFileError("File không có luồng hình ảnh.")
    if require_audio and not audio_stream:
        raise InvalidVideoFileError("File không có luồng âm thanh.")

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    duration_raw = format_info.get("duration") or video_stream.get("duration")
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError):
        duration = 0.0
    if width <= 0 or height <= 0:
        raise InvalidVideoFileError("Không xác định được kích thước hình ảnh của video.")
    if duration <= 0:
        raise InvalidVideoFileError("Không xác định được thời lượng video.")

    return {
        "path": str(source),
        "file_size": file_size,
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
        "has_audio": audio_stream is not None,
        "video_codec": str(video_stream.get("codec_name") or ""),
        "audio_codec": str(audio_stream.get("codec_name") or "") if audio_stream else "",
    }
