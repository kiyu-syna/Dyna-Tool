from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import ctypes
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from core.runtime_paths import state_dir, temp_dir
from core.utils import logger
from services.publishing.video_validation_service import (
    FFprobeNotFoundError,
    InvalidVideoFileError,
    resolve_ffprobe,
    validate_video_file,
)
from services.runtime.workload_coordinator import WorkloadCancelled, workload_slot


BUSY_STATUSES = {"detecting", "transcribing", "translating", "dubbing", "rendering"}
SUPPORTED_MODELS = {"tiny", "base", "small", "medium", "large-v3"}
SUPPORTED_SUBTITLE_TRACKS = {"source", "translated"}
MAX_SUBTITLE_FONT_SIZE = 300
VIENEU_EXPECTED_MODEL_BYTES = 300_000_000
EDGE_TTS_VOICES = [
    {"id": "vi-VN-HoaiMyNeural", "locale": "vi-VN", "gender": "female", "name": "Hoài My"},
    {"id": "vi-VN-NamMinhNeural", "locale": "vi-VN", "gender": "male", "name": "Nam Minh"},
    {"id": "zh-CN-XiaoxiaoNeural", "locale": "zh-CN", "gender": "female", "name": "Xiaoxiao"},
    {"id": "zh-CN-YunxiNeural", "locale": "zh-CN", "gender": "male", "name": "Yunxi"},
    {"id": "en-US-JennyNeural", "locale": "en-US", "gender": "female", "name": "Jenny"},
    {"id": "en-US-GuyNeural", "locale": "en-US", "gender": "male", "name": "Guy"},
    {"id": "ja-JP-NanamiNeural", "locale": "ja-JP", "gender": "female", "name": "Nanami"},
    {"id": "ja-JP-KeitaNeural", "locale": "ja-JP", "gender": "male", "name": "Keita"},
    {"id": "ko-KR-SunHiNeural", "locale": "ko-KR", "gender": "female", "name": "Sun-Hi"},
    {"id": "ko-KR-InJoonNeural", "locale": "ko-KR", "gender": "male", "name": "InJoon"},
    {"id": "th-TH-PremwadeeNeural", "locale": "th-TH", "gender": "female", "name": "Premwadee"},
    {"id": "th-TH-NiwatNeural", "locale": "th-TH", "gender": "male", "name": "Niwat"},
    {"id": "id-ID-GadisNeural", "locale": "id-ID", "gender": "female", "name": "Gadis"},
    {"id": "id-ID-ArdiNeural", "locale": "id-ID", "gender": "male", "name": "Ardi"},
]
VIENEU_TTS_VOICES = [
    {"id": "Minh Đức", "locale": "vi-VN", "gender": "male", "name": "Minh Đức", "region": "Bắc", "default_style": "tin_tuc"},
    {"id": "Phạm Tuyên", "locale": "vi-VN", "gender": "male", "name": "Phạm Tuyên", "region": "Bắc", "default_style": "tu_nhien"},
    {"id": "Thái Sơn", "locale": "vi-VN", "gender": "male", "name": "Thái Sơn", "region": "Nam", "default_style": "doc_truyen"},
    {"id": "Xuân Vĩnh", "locale": "vi-VN", "gender": "male", "name": "Xuân Vĩnh", "region": "Nam", "default_style": "tu_nhien"},
    {"id": "Thanh Bình", "locale": "vi-VN", "gender": "male", "name": "Thanh Bình", "region": "Bắc", "default_style": "doc_truyen"},
    {"id": "Trúc Ly", "locale": "vi-VN", "gender": "female", "name": "Trúc Ly", "region": "Bắc", "default_style": "tu_nhien"},
    {"id": "Ngọc Linh", "locale": "vi-VN", "gender": "female", "name": "Ngọc Linh", "region": "Bắc", "default_style": "doc_truyen"},
    {"id": "Đoan Trang", "locale": "vi-VN", "gender": "female", "name": "Đoan Trang", "region": "Bắc", "default_style": "tu_nhien"},
    {"id": "Mai Anh", "locale": "vi-VN", "gender": "female", "name": "Mai Anh", "region": "Bắc", "default_style": "tin_tuc"},
    {"id": "Thục Đoan", "locale": "vi-VN", "gender": "female", "name": "Thục Đoan", "region": "Nam", "default_style": "doc_truyen"},
    {"id": "Minh Triết", "locale": "vi-VN", "gender": "male", "name": "Minh Triết", "region": "Nam", "default_style": "tin_tuc"},
    {"id": "Thùy Dung", "locale": "vi-VN", "gender": "female", "name": "Thùy Dung", "region": "Nam", "default_style": "tin_tuc"},
    {"id": "Quang Sơn", "locale": "vi-VN", "gender": "male", "name": "Quang Sơn", "region": "Trung", "default_style": "tu_nhien"},
    {"id": "Ngọc Trân", "locale": "vi-VN", "gender": "female", "name": "Ngọc Trân", "region": "Trung", "default_style": "tu_nhien"},
]
TTS_PROVIDERS = {"edge", "vieneu"}
TTS_STYLES = {"tu_nhien", "tin_tuc", "doc_truyen"}
TTS_VOICES_BY_PROVIDER = {
    "edge": EDGE_TTS_VOICES,
    "vieneu": VIENEU_TTS_VOICES,
}
TTS_VOICE_IDS_BY_PROVIDER = {
    provider: {voice["id"] for voice in voices}
    for provider, voices in TTS_VOICES_BY_PROVIDER.items()
}
PROJECT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_CUDA_DLL_HANDLES: list[Any] = []
_CUDA_DLL_PATHS: set[str] = set()


class VideoAiError(RuntimeError):
    pass


class VideoAiNotFoundError(VideoAiError):
    pass


class VideoAiBusyError(VideoAiError):
    pass


class VideoAiDependencyError(VideoAiError):
    pass


class VideoAiCancelled(VideoAiError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _finite_number(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: Any, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, _finite_number(value, default=minimum)))


def _clean_subtitle_text(value: Any, *, limit: int = 12_000) -> str:
    text = str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()[:limit]


def _normalized_subtitles(subtitles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(subtitles) > 5_000:
        raise VideoAiError("Một dự án chỉ hỗ trợ tối đa 5.000 dòng phụ đề.")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(subtitles, start=1):
        start = max(0.0, _finite_number(item.get("start")))
        end = _finite_number(item.get("end"), default=start + 2.0)
        if end <= start:
            raise VideoAiError(f"Dòng phụ đề {index} có thời gian kết thúc không hợp lệ.")
        normalized.append(
            {
                "id": str(item.get("id") or index)[:64],
                "start": round(start, 3),
                "end": round(end, 3),
                "text": _clean_subtitle_text(item.get("text")),
                "translated_text": _clean_subtitle_text(item.get("translated_text")),
            }
        )
    normalized.sort(key=lambda item: (item["start"], item["end"]))
    return normalized


def _normalized_style(style: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "font_name": str((style or {}).get("font_name") or "Arial")[:80],
        "font_size": int(
            _clamp((style or {}).get("font_size", 42), 12, MAX_SUBTITLE_FONT_SIZE)
        ),
        "margin_v": int(_clamp((style or {}).get("margin_v", 54), 0, 500)),
        "outline": _clamp((style or {}).get("outline", 2), 0, 8),
        "primary_color": str((style or {}).get("primary_color") or "&H00FFFFFF"),
    }


def _normalized_dubbing(value: dict[str, Any] | None) -> dict[str, Any]:
    raw = value or {}
    provider = str(raw.get("provider") or "edge").strip().lower()
    if provider not in TTS_PROVIDERS:
        provider = "edge"
    default_voice = "Phạm Tuyên" if provider == "vieneu" else "vi-VN-HoaiMyNeural"
    voice = str(raw.get("voice") or default_voice).strip()
    if voice not in TTS_VOICE_IDS_BY_PROVIDER[provider]:
        voice = default_voice
    style = str(raw.get("style") or "tu_nhien").strip().lower()
    if style not in TTS_STYLES:
        style = "tu_nhien"
    speed_warnings = []
    for item in raw.get("speed_warnings") or []:
        if not isinstance(item, dict):
            continue
        speed_warnings.append(
            {
                "id": str(item.get("id") or "")[:64],
                "speed": round(_clamp(item.get("speed", 1), 1, 20), 2),
                "slot_duration": round(
                    _clamp(item.get("slot_duration", 0), 0, 86_400),
                    3,
                ),
                "speech_duration": round(
                    _clamp(item.get("speech_duration", 0), 0, 86_400),
                    3,
                ),
            }
        )
    return {
        "enabled": bool(raw.get("enabled", False)),
        "provider": provider,
        "voice": voice,
        "style": style,
        "rate": int(_clamp(raw.get("rate", 0), -50, 50)),
        "volume": int(_clamp(raw.get("volume", 100), 0, 200)),
        "original_volume": int(_clamp(raw.get("original_volume", 18), 0, 100)),
        "audio_path": str(raw.get("audio_path") or ""),
        "generated_at": str(raw.get("generated_at") or ""),
        "line_count": max(0, int(_finite_number(raw.get("line_count"), default=0))),
        "max_speed": round(_clamp(raw.get("max_speed", 1), 1, 20), 2),
        "speed_warnings": speed_warnings[:5_000],
    }


def _clear_generated_dubbing(project: dict[str, Any]) -> None:
    options = _normalized_dubbing(project.get("dubbing_options"))
    options.update(
        {
            "audio_path": "",
            "generated_at": "",
            "line_count": 0,
            "max_speed": 1,
            "speed_warnings": [],
        }
    )
    project["dubbing_options"] = options


def _atempo_filters(speed: float) -> list[str]:
    remaining = max(0.5, float(speed))
    filters: list[str] = []
    while remaining > 2.0:
        filters.append("atempo=2")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.6f}")
    return filters


def _probe_audio_duration(ffprobe: str, path: Path) -> float:
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if completed.returncode != 0:
        return 0.0
    return max(0.0, _finite_number(completed.stdout.strip()))


def _directory_size(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _format_ass_time(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def _ass_text(value: str) -> str:
    return (
        _clean_subtitle_text(value)
        .replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", r"\N")
    )


def _write_ass(
    path: Path,
    subtitles: Iterable[dict[str, Any]],
    track: str,
    *,
    width: int,
    height: int,
    style: dict[str, Any],
    subtitle_region: dict[str, Any] | None = None,
) -> int:
    font_name = re.sub(r"[\r\n,]+", " ", str(style.get("font_name") or "Arial")).strip()[:80]
    font_size = int(_clamp(style.get("font_size", 42), 12, MAX_SUBTITLE_FONT_SIZE))
    margin_v = int(_clamp(style.get("margin_v", 54), 0, max(100, height)))
    outline = _clamp(style.get("outline", 2), 0, 8)
    primary = str(style.get("primary_color") or "&H00FFFFFF")
    if not re.fullmatch(r"&H[0-9A-Fa-f]{8}", primary):
        primary = "&H00FFFFFF"
    region = subtitle_region or {}
    use_region = bool(region.get("enabled"))
    if use_region:
        region_x = _clamp(region.get("x", 0.05), 0.0, 0.98)
        region_y = _clamp(region.get("y", 0.78), 0.0, 0.98)
        region_width = _clamp(region.get("width", 0.9), 0.02, 1.0 - region_x)
        region_height = _clamp(region.get("height", 0.17), 0.02, 1.0 - region_y)
        margin_l = max(12, int(round(width * region_x)) + 12)
        margin_r = max(12, int(round(width * (1.0 - region_x - region_width))) + 12)
        alignment = 5
        position_override = (
            r"{\an5\pos("
            f"{width * (region_x + region_width / 2.0):.1f},"
            f"{height * (region_y + region_height / 2.0):.1f}"
            ")}"
        )
    else:
        margin_l = 24
        margin_r = 24
        alignment = 2
        position_override = ""
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {max(16, int(width))}",
        f"PlayResY: {max(16, int(height))}",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        (
            f"Style: Dyna,{font_name or 'Arial'},{font_size},{primary},&H00FFFFFF,"
            f"&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{outline:g},0,"
            f"{alignment},{margin_l},{margin_r},{margin_v},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events: list[str] = []
    for item in subtitles:
        text = _subtitle_track_text(item, track)
        if not text:
            continue
        start = max(0.0, _finite_number(item.get("start")))
        end = max(start + 0.05, _finite_number(item.get("end"), default=start + 2.0))
        events.append(
            f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},"
            f"Dyna,,0,0,0,,{position_override}{_ass_text(text)}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(header + events) + "\n", encoding="utf-8-sig")
    return len(events)


def _subtitle_track_text(item: dict[str, Any], track: str) -> str:
    if track == "translated":
        translated = _clean_subtitle_text(item.get("translated_text"))
        if translated:
            return translated
    return _clean_subtitle_text(item.get("text"))


def _escape_ffmpeg_filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    return (
        value.replace(":", r"\:")
        .replace("'", r"\'")
        .replace("[", r"\[")
        .replace("]", r"\]")
        .replace(",", r"\,")
        .replace(";", r"\;")
    )


def _normalized_blur(value: dict[str, Any] | None) -> dict[str, Any]:
    raw = value or {}
    x = _clamp(raw.get("x", 0.05), 0.0, 0.98)
    y = _clamp(raw.get("y", 0.78), 0.0, 0.98)
    width = _clamp(raw.get("width", 0.90), 0.02, 1.0 - x)
    height = _clamp(raw.get("height", 0.17), 0.02, 1.0 - y)
    return {
        "enabled": bool(raw.get("enabled", False)),
        "x": round(x, 4),
        "y": round(y, 4),
        "width": round(width, 4),
        "height": round(height, 4),
        "strength": int(_clamp(raw.get("strength", 22), 2, 60)),
    }


def _build_filter_graph(ass_path: Path, blur: dict[str, Any]) -> str:
    escaped_ass = _escape_ffmpeg_filter_path(ass_path)
    ass_filter = f"ass=filename='{escaped_ass}'"
    if not blur.get("enabled"):
        return f"[0:v]{ass_filter}[vout]"
    x = blur["x"]
    y = blur["y"]
    width = blur["width"]
    height = blur["height"]
    strength = blur["strength"]
    chroma_strength = max(1, strength // 2)
    boxblur = (
        f"boxblur=luma_radius='max(0,min({strength},min(w,h)/2-1))':luma_power=1:"
        f"chroma_radius='max(0,min({chroma_strength},min(cw,ch)/2-1))':chroma_power=1"
    )
    return (
        "[0:v]split=2[base][blur_source];"
        f"[blur_source]crop=w=iw*{width}:h=ih*{height}:x=iw*{x}:y=ih*{y},"
        f"{boxblur}[blurred];"
        f"[base][blurred]overlay=x=main_w*{x}:y=main_h*{y}[clean];"
        f"[clean]{ass_filter}[vout]"
    )


def _translation_chunks(
    subtitles: list[dict[str, Any]],
    *,
    max_items: int = 20,
    max_characters: int = 4_000,
) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    characters = 0
    for item in subtitles:
        size = len(_clean_subtitle_text(item.get("text"))) + 40
        if current and (len(current) >= max_items or characters + size > max_characters):
            chunks.append(current)
            current = []
            characters = 0
        current.append(item)
        characters += size
    if current:
        chunks.append(current)
    return chunks


def _merge_detection_boxes(
    boxes: list[tuple[int, int, int, int]],
    *,
    frame_width: int,
    frame_height: int,
    allow_stacked: bool = True,
) -> list[tuple[int, int, int, int]]:
    merged = list(boxes)
    changed = True
    while changed:
        changed = False
        for left_index in range(len(merged)):
            ax, ay, aw, ah = merged[left_index]
            for right_index in range(left_index + 1, len(merged)):
                bx, by, bw, bh = merged[right_index]
                horizontal_overlap = max(0, min(ax + aw, bx + bw) - max(ax, bx))
                vertical_overlap = max(0, min(ay + ah, by + bh) - max(ay, by))
                horizontal_gap = max(0, max(ax, bx) - min(ax + aw, bx + bw))
                vertical_gap = max(0, max(ay, by) - min(ay + ah, by + bh))
                same_line = (
                    vertical_overlap >= min(ah, bh) * 0.35
                    and horizontal_gap <= frame_width * 0.08
                )
                stacked_lines = (
                    allow_stacked
                    and ah <= frame_height * 0.10
                    and bh <= frame_height * 0.10
                    and horizontal_overlap >= min(aw, bw) * 0.25
                    and vertical_gap <= frame_height * 0.035
                    and abs((ax + aw / 2) - (bx + bw / 2)) <= frame_width * 0.20
                )
                if not same_line and not stacked_lines:
                    continue
                x1 = min(ax, bx)
                y1 = min(ay, by)
                x2 = max(ax + aw, bx + bw)
                y2 = max(ay + ah, by + bh)
                merged[left_index] = (x1, y1, x2 - x1, y2 - y1)
                merged.pop(right_index)
                changed = True
                break
            if changed:
                break
    return merged


def _detect_subtitle_region_from_frames(
    frames: Iterable[Any],
    *,
    include_debug: bool = False,
) -> dict[str, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise VideoAiDependencyError(
            "Chưa cài OpenCV và NumPy để tự tìm vùng phụ đề."
        ) from exc

    prepared: list[Any] = []
    target_size: tuple[int, int] | None = None
    for raw_frame in frames:
        if raw_frame is None or not hasattr(raw_frame, "shape") or len(raw_frame.shape) < 2:
            continue
        raw_height, raw_width = raw_frame.shape[:2]
        if raw_height < 32 or raw_width < 32:
            continue
        if target_size is None:
            scale = min(1.0, 960.0 / max(raw_width, raw_height))
            target_size = (
                max(32, int(round(raw_width * scale))),
                max(32, int(round(raw_height * scale))),
            )
        frame = cv2.resize(raw_frame, target_size, interpolation=cv2.INTER_AREA)
        prepared.append(frame)
    if not prepared or target_size is None:
        return {
            "detected": False,
            "confidence": 0,
            "sampled_frames": 0,
            "candidate_frames": 0,
            "region": None,
        }

    frame_width, frame_height = target_size
    accumulation = np.zeros((frame_height, frame_width), dtype=np.float32)
    all_boxes: list[tuple[int, int, int, int, int]] = []
    candidate_frames = 0
    close_width = max(9, int(round(frame_width * 0.025)))
    close_height = max(3, int(round(frame_height * 0.004)))
    lower_start = int(round(frame_height * 0.32))

    for frame_index, frame in enumerate(prepared):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        horizontal_edges = cv2.convertScaleAbs(
            cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)
        )
        _, binary = cv2.threshold(
            horizontal_edges,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )
        binary[:lower_start, :] = 0
        merged_edges = cv2.morphologyEx(
            binary,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (close_width, close_height),
            ),
            iterations=2,
        )
        merged_edges = cv2.morphologyEx(
            merged_edges,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 2)),
        )
        frame_boxes: list[tuple[int, int, int, int]] = []
        contours = cv2.findContours(
            merged_edges,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )[0]
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            if (
                width < frame_width * 0.08
                or width > frame_width * 0.98
                or height < frame_height * 0.008
                or height > frame_height * 0.18
                or width / max(1, height) < 1.45
            ):
                continue
            edge_density = (
                cv2.countNonZero(binary[y:y + height, x:x + width])
                / max(1, width * height)
            )
            if edge_density < 0.03 or edge_density > 0.78:
                continue
            frame_boxes.append((x, y, width, height))
        _, bright = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
        bright[:lower_start, :] = 0
        bright_regions = cv2.morphologyEx(
            bright,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (
                    max(9, int(round(frame_width * 0.03))),
                    max(3, int(round(frame_height * 0.006))),
                ),
            ),
        )
        bright_regions = cv2.morphologyEx(
            bright_regions,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
        )
        for contour in cv2.findContours(
            bright_regions,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )[0]:
            x, y, width, height = cv2.boundingRect(contour)
            if (
                width < frame_width * 0.08
                or width > frame_width * 0.98
                or height < frame_height * 0.008
                or height > frame_height * 0.18
                or width / max(1, height) < 1.45
            ):
                continue
            bright_density = (
                cv2.countNonZero(bright[y:y + height, x:x + width])
                / max(1, width * height)
            )
            if 0.03 <= bright_density <= 0.80:
                frame_boxes.append((x, y, width, height))
        frame_boxes = _merge_detection_boxes(
            frame_boxes,
            frame_width=frame_width,
            frame_height=frame_height,
            allow_stacked=False,
        )
        frame_boxes = [
            box for box in frame_boxes if box[3] <= frame_height * 0.20
        ]
        if not frame_boxes:
            continue
        candidate_frames += 1
        frame_mask = np.zeros((frame_height, frame_width), dtype=np.uint8)
        for x, y, width, height in frame_boxes:
            all_boxes.append((frame_index, x, y, width, height))
            cv2.rectangle(
                frame_mask,
                (x, y),
                (min(frame_width - 1, x + width), min(frame_height - 1, y + height)),
                255,
                thickness=-1,
            )
        accumulation += frame_mask.astype(np.float32) / 255.0

    sampled_frames = len(prepared)
    if candidate_frames < 2:
        return {
            "detected": False,
            "confidence": 0,
            "sampled_frames": sampled_frames,
            "candidate_frames": candidate_frames,
            "region": None,
        }

    frequency = accumulation / max(1, sampled_frames)
    support_threshold = max(2 / sampled_frames, 0.12)
    stable = (frequency >= support_threshold).astype(np.uint8) * 255
    stable = cv2.morphologyEx(
        stable,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (
                max(7, int(round(frame_width * 0.035))),
                max(3, int(round(frame_height * 0.008))),
            ),
        ),
    )
    stable_boxes: list[tuple[int, int, int, int]] = []
    for contour in cv2.findContours(
        stable,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )[0]:
        x, y, width, height = cv2.boundingRect(contour)
        if (
            width >= frame_width * 0.06
            and height >= frame_height * 0.008
            and height <= frame_height * 0.20
            and y + height / 2 >= frame_height * 0.34
        ):
            stable_boxes.append((x, y, width, height))
    stable_boxes = _merge_detection_boxes(
        stable_boxes,
        frame_width=frame_width,
        frame_height=frame_height,
    )
    if not stable_boxes:
        return {
            "detected": False,
            "confidence": 0,
            "sampled_frames": sampled_frames,
            "candidate_frames": candidate_frames,
            "region": None,
        }

    best: tuple[float, tuple[int, int, int, int], float] | None = None
    debug_candidates: list[dict[str, Any]] = []
    for box in stable_boxes:
        x, y, width, height = box
        local_frequency = frequency[y:y + height, x:x + width]
        support = float(local_frequency.mean()) if local_frequency.size else 0.0
        center_x = (x + width / 2) / frame_width
        center_y = (y + height / 2) / frame_height
        center_score = max(0.0, 1.0 - abs(center_x - 0.5) / 0.5)
        width_score = min(1.0, width / max(1.0, frame_width * 0.45))
        lower_score = max(0.0, min(1.0, (center_y - 0.34) / 0.58))
        support_score = min(1.0, support / 0.55)
        compact_score = max(
            0.0,
            1.0 - max(0.0, height / frame_height - 0.04) / 0.16,
        )
        score = (
            support_score * 0.34
            + center_score * 0.28
            + width_score * 0.15
            + lower_score * 0.10
            + compact_score * 0.13
        )
        if include_debug:
            debug_candidates.append(
                {
                    "x": round(x / frame_width, 4),
                    "y": round(y / frame_height, 4),
                    "width": round(width / frame_width, 4),
                    "height": round(height / frame_height, 4),
                    "score": round(score, 4),
                    "support": round(support, 4),
                    "center": round(center_score, 4),
                    "compact": round(compact_score, 4),
                }
            )
        if best is None or score > best[0]:
            best = (score, box, support)

    assert best is not None
    score, (stable_x, stable_y, stable_width, stable_height), _support = best
    matching_boxes = []
    matching_frame_indexes: set[int] = set()
    for frame_index, x, y, width, height in all_boxes:
        horizontal_overlap = max(
            0,
            min(stable_x + stable_width, x + width) - max(stable_x, x),
        )
        vertical_gap = max(
            0,
            max(stable_y, y) - min(stable_y + stable_height, y + height),
        )
        center_distance_y = abs(
            (stable_y + stable_height / 2) - (y + height / 2)
        )
        if (
            horizontal_overlap >= min(stable_width, width) * 0.25
            and vertical_gap <= frame_height * 0.035
            and center_distance_y <= max(
                frame_height * 0.07,
                (stable_height + height) * 0.65,
            )
        ):
            matching_boxes.append((x, y, width, height))
            matching_frame_indexes.add(frame_index)
    if matching_boxes:
        center_y = float(np.median([
            box[1] + box[3] / 2 for box in matching_boxes
        ]))
        detected_height = float(np.percentile(
            [box[3] for box in matching_boxes],
            90,
        ))
        x1 = float(stable_x)
        x2 = float(stable_x + stable_width)
        y1 = center_y - detected_height / 2
        y2 = center_y + detected_height / 2
    else:
        x1, y1 = float(stable_x), float(stable_y)
        x2 = float(stable_x + stable_width)
        y2 = float(stable_y + stable_height)

    x_margin = frame_width * 0.025
    y_margin = frame_height * 0.018
    x1 = max(0.0, x1 - x_margin)
    y1 = max(0.0, y1 - y_margin)
    x2 = min(float(frame_width), x2 + x_margin)
    y2 = min(float(frame_height), y2 + y_margin)
    frame_support = len(matching_frame_indexes) / max(1, sampled_frames)
    score = score * 0.78 + min(1.0, frame_support / 0.55) * 0.22
    region = _normalized_blur(
        {
            "enabled": True,
            "x": x1 / frame_width,
            "y": y1 / frame_height,
            "width": (x2 - x1) / frame_width,
            "height": (y2 - y1) / frame_height,
            "strength": 22,
        }
    )
    result = {
        "detected": True,
        "confidence": int(round(_clamp(score * 100, 1, 99))),
        "sampled_frames": sampled_frames,
        "candidate_frames": len(matching_frame_indexes),
        "region": region,
    }
    if include_debug:
        result["debug_candidates"] = sorted(
            debug_candidates,
            key=lambda item: item["score"],
            reverse=True,
        )
    return result


def _configure_cuda_dll_search() -> None:
    if os.name != "nt":
        return
    candidates: list[Path] = []
    try:
        cublas_spec = importlib.util.find_spec("nvidia.cublas")
    except (ImportError, ModuleNotFoundError, ValueError):
        cublas_spec = None
    if cublas_spec and cublas_spec.submodule_search_locations:
        candidates.extend(Path(location) / "bin" for location in cublas_spec.submodule_search_locations)
    ctranslate_spec = importlib.util.find_spec("ctranslate2")
    if ctranslate_spec and ctranslate_spec.origin:
        candidates.append(Path(ctranslate_spec.origin).parent)
    candidates.append(Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin")
    cuda_path = str(os.environ.get("CUDA_PATH") or "").strip()
    if cuda_path:
        candidates.append(Path(cuda_path) / "bin")

    for candidate in candidates:
        if not candidate.is_dir():
            continue
        normalized = str(candidate.resolve())
        if normalized.lower() in _CUDA_DLL_PATHS:
            continue
        try:
            _CUDA_DLL_HANDLES.append(os.add_dll_directory(normalized))
        except (AttributeError, OSError):
            continue
        _CUDA_DLL_PATHS.add(normalized.lower())
        current_path = str(os.environ.get("PATH") or "")
        if normalized.lower() not in current_path.lower().split(os.pathsep):
            os.environ["PATH"] = normalized + os.pathsep + current_path


def _cuda_runtime_status() -> dict[str, Any]:
    if importlib.util.find_spec("ctranslate2") is None:
        return {"devices": 0, "ready": False, "missing": []}
    _configure_cuda_dll_search()
    try:
        import ctranslate2

        devices = int(ctranslate2.get_cuda_device_count())
    except Exception:
        return {"devices": 0, "ready": False, "missing": []}
    if devices < 1:
        return {"devices": 0, "ready": False, "missing": []}
    if os.name != "nt":
        return {"devices": devices, "ready": True, "missing": []}

    missing: list[str] = []
    for library in ("cublas64_12.dll", "cudnn64_9.dll"):
        try:
            ctypes.WinDLL(library)
        except OSError:
            missing.append(library)
    return {"devices": devices, "ready": not missing, "missing": missing}


class VideoAiService:
    def __init__(
        self,
        translator: Any | None = None,
        *,
        project_root: str | Path | None = None,
        scratch_root: str | Path | None = None,
        video_validator: Callable[..., dict[str, Any]] = validate_video_file,
        rate_limit_waiter: Callable[[threading.Event, float], bool] | None = None,
    ) -> None:
        self.translator = translator
        self.project_root = Path(project_root or state_dir() / "video-ai")
        self.scratch_root = Path(scratch_root or temp_dir() / "video-ai")
        self.video_validator = video_validator
        self._rate_limit_waiter = rate_limit_waiter or (
            lambda cancel_event, seconds: cancel_event.wait(seconds)
        )
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._vieneu_engine: Any | None = None
        self._vieneu_lock = threading.Lock()
        self._vieneu_prepare_thread: threading.Thread | None = None
        self._vieneu_runtime: dict[str, Any] = {
            "state": "idle",
            "stage": "",
            "error": "",
        }

    def capabilities(self) -> dict[str, Any]:
        try:
            ffmpeg = self._resolve_ffmpeg()
            ffmpeg_ready = True
        except VideoAiDependencyError:
            ffmpeg = ""
            ffmpeg_ready = False
        try:
            ffprobe = resolve_ffprobe()
            ffprobe_ready = True
        except FFprobeNotFoundError:
            ffprobe = ""
            ffprobe_ready = False

        faster_whisper_ready = importlib.util.find_spec("faster_whisper") is not None
        vision_ready = (
            importlib.util.find_spec("cv2") is not None
            and importlib.util.find_spec("numpy") is not None
        )
        edge_tts_ready = importlib.util.find_spec("edge_tts") is not None
        vieneu_tts_ready = importlib.util.find_spec("vieneu") is not None
        cuda = _cuda_runtime_status() if faster_whisper_ready else {
            "devices": 0,
            "ready": False,
            "missing": [],
        }
        return {
            "ffmpeg": {"ready": ffmpeg_ready, "path": ffmpeg},
            "ffprobe": {"ready": ffprobe_ready, "path": ffprobe},
            "asr": {
                "ready": faster_whisper_ready,
                "engine": "faster-whisper",
                "cuda_devices": cuda["devices"],
                "cuda_ready": cuda["ready"],
                "device": "cuda" if cuda["ready"] else "cpu",
                "cuda_missing": cuda["missing"],
                "models": sorted(SUPPORTED_MODELS, key=("tiny", "base", "small", "medium", "large-v3").index),
                "install_command": r".\.venv\Scripts\python.exe -m pip install -r requirements-video-ai.txt",
            },
            "translation": {"ready": self.translator is not None, "provider": "DynaAI"},
            "vision": {
                "ready": vision_ready,
                "engine": "OpenCV",
                "install_command": r".\.venv\Scripts\python.exe -m pip install -r requirements-video-ai.txt",
            },
            "tts": {
                "ready": edge_tts_ready or vieneu_tts_ready,
                "engine": "Edge-TTS / VieNeu-TTS",
                "providers": [
                    {
                        "id": "edge",
                        "name": "Edge-TTS",
                        "ready": edge_tts_ready,
                        "offline": False,
                        "voices": EDGE_TTS_VOICES,
                        "styles": [],
                    },
                    {
                        "id": "vieneu",
                        "name": "VieNeu-TTS",
                        "ready": vieneu_tts_ready,
                        "offline": True,
                        "voices": VIENEU_TTS_VOICES,
                        "styles": [
                            {"id": "tu_nhien", "name": "Tự nhiên"},
                            {"id": "tin_tuc", "name": "Tin tức"},
                            {"id": "doc_truyen", "name": "Kể chuyện"},
                        ],
                    },
                ],
                "voices": EDGE_TTS_VOICES,
                "install_command": r".\.venv\Scripts\python.exe -m pip install -r requirements-video-ai.txt",
            },
        }

    def tts_runtime_status(self, provider: str = "vieneu") -> dict[str, Any]:
        normalized = str(provider or "").strip().lower()
        if normalized != "vieneu":
            ready = importlib.util.find_spec("edge_tts") is not None
            return {
                "provider": "edge",
                "state": "ready" if ready else "not_installed",
                "ready": ready,
                "progress": 100 if ready else 0,
                "stage": "Edge-TTS đã sẵn sàng" if ready else "Chưa cài Edge-TTS",
                "error": "",
                "cache_path": "",
                "size_bytes": 0,
                "expected_size_bytes": 0,
            }

        model_root = self._vieneu_model_root()
        size_bytes = _directory_size(model_root)
        installed = importlib.util.find_spec("vieneu") is not None
        with self._lock:
            runtime = dict(self._vieneu_runtime)
            engine_ready = self._vieneu_engine is not None
        if not installed:
            state = "not_installed"
            stage = "Chưa cài VieNeu-TTS"
            progress = 0
        elif engine_ready:
            state = "ready"
            stage = "VieNeu-TTS đã sẵn sàng trên CPU"
            progress = 100
        elif runtime["state"] == "loading":
            state = "loading"
            stage = runtime["stage"] or "Đang tải hoặc nạp model VieNeu-TTS"
            progress = min(
                98,
                max(2, int(size_bytes / VIENEU_EXPECTED_MODEL_BYTES * 100)),
            )
        elif runtime["state"] == "failed":
            state = "failed"
            stage = "Không nạp được VieNeu-TTS"
            progress = min(
                100,
                int(size_bytes / VIENEU_EXPECTED_MODEL_BYTES * 100),
            )
        elif size_bytes >= int(VIENEU_EXPECTED_MODEL_BYTES * 0.8):
            state = "downloaded"
            stage = "Model đã tải · sẵn sàng để nạp"
            progress = 100
        else:
            state = "not_downloaded"
            stage = "Model sẽ được tải ở lần sử dụng đầu tiên"
            progress = min(
                99,
                int(size_bytes / VIENEU_EXPECTED_MODEL_BYTES * 100),
            )
        return {
            "provider": "vieneu",
            "state": state,
            "ready": state == "ready",
            "progress": progress,
            "stage": stage,
            "error": str(runtime.get("error") or ""),
            "cache_path": str(model_root),
            "size_bytes": size_bytes,
            "expected_size_bytes": VIENEU_EXPECTED_MODEL_BYTES,
        }

    def start_tts_prepare(self, provider: str = "vieneu") -> dict[str, Any]:
        normalized = str(provider or "").strip().lower()
        if normalized != "vieneu":
            return self.tts_runtime_status(normalized)
        if importlib.util.find_spec("vieneu") is None:
            raise VideoAiDependencyError(
                "Chưa cài VieNeu-TTS. Hãy cài requirements-video-ai.txt rồi khởi động lại Dyna."
            )
        with self._lock:
            if self._vieneu_engine is not None:
                return self.tts_runtime_status("vieneu")
            if self._vieneu_prepare_thread and self._vieneu_prepare_thread.is_alive():
                return self.tts_runtime_status("vieneu")
            self._vieneu_runtime = {
                "state": "loading",
                "stage": "Đang tải hoặc nạp VieNeu-TTS trên CPU",
                "error": "",
            }

            def prepare() -> None:
                try:
                    self._ensure_vieneu_engine()
                except Exception:
                    logger.exception("[Video AI] Không thể chuẩn bị VieNeu-TTS")
                finally:
                    with self._lock:
                        self._vieneu_prepare_thread = None

            thread = threading.Thread(
                target=prepare,
                daemon=True,
                name="video-ai-vieneu-prepare",
            )
            self._vieneu_prepare_thread = thread
            thread.start()
        return self.tts_runtime_status("vieneu")

    def create_tts_preview(
        self,
        *,
        provider: str,
        voice: str,
        style: str = "tu_nhien",
        text: str = "",
    ) -> dict[str, Any]:
        options = _normalized_dubbing(
            {
                "provider": provider,
                "voice": voice,
                "style": style,
            }
        )
        sample_text = _clean_subtitle_text(
            text or "Xin chào, đây là giọng đọc mẫu trong Dyna.",
            limit=300,
        )
        preview_id = uuid.uuid4().hex
        preview_root = self.scratch_root / "tts-previews"
        preview_root.mkdir(parents=True, exist_ok=True)
        try:
            previews = sorted(
                (item for item in preview_root.iterdir() if item.is_file()),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for stale in previews[49:]:
                stale.unlink(missing_ok=True)
        except OSError:
            pass
        if options["provider"] == "vieneu":
            engine = self._ensure_vieneu_engine()
            target = preview_root / f"{preview_id}.wav"
            try:
                with self._vieneu_lock:
                    audio = engine.infer(
                        sample_text,
                        voice=options["voice"],
                        style=options["style"],
                    )
                    engine.save(audio, str(target))
            except Exception as exc:
                raise VideoAiError(f"Không tạo được bản nghe thử VieNeu-TTS: {exc}") from exc
        else:
            if importlib.util.find_spec("edge_tts") is None:
                raise VideoAiDependencyError("Chưa cài Edge-TTS.")
            import edge_tts

            target = preview_root / f"{preview_id}.mp3"

            async def synthesize() -> None:
                communicator = edge_tts.Communicate(
                    sample_text,
                    options["voice"],
                    rate=f"{options['rate']:+d}%",
                )
                await communicator.save(str(target))

            try:
                asyncio.run(synthesize())
            except Exception as exc:
                raise VideoAiError(f"Không tạo được bản nghe thử Edge-TTS: {exc}") from exc
        if not target.is_file() or target.stat().st_size <= 128:
            raise VideoAiError("Không nhận được âm thanh nghe thử.")
        return {
            "preview_id": preview_id,
            "provider": options["provider"],
            "voice": options["voice"],
        }

    def tts_preview_path(self, preview_id: str) -> Path:
        normalized = str(preview_id or "").strip().lower()
        if not PROJECT_ID_PATTERN.fullmatch(normalized):
            raise VideoAiNotFoundError("Mã bản nghe thử không hợp lệ.")
        preview_root = self.scratch_root / "tts-previews"
        for suffix in (".wav", ".mp3"):
            candidate = preview_root / f"{normalized}{suffix}"
            if candidate.is_file():
                return candidate
        raise VideoAiNotFoundError("Không tìm thấy bản nghe thử giọng đọc.")

    def _vieneu_model_root(self) -> Path:
        return self.project_root.parent / "video-ai-models" / "huggingface"

    def _ensure_vieneu_engine(self) -> Any:
        from vieneu import Vieneu

        with self._vieneu_lock:
            if self._vieneu_engine is not None:
                return self._vieneu_engine
            model_root = self._vieneu_model_root()
            model_root.mkdir(parents=True, exist_ok=True)
            os.environ["HF_HOME"] = str(model_root)
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            with self._lock:
                self._vieneu_runtime = {
                    "state": "loading",
                    "stage": "Đang tải hoặc nạp VieNeu-TTS trên CPU",
                    "error": "",
                }
            try:
                engine = Vieneu(backend="onnx")
            except Exception as exc:
                with self._lock:
                    self._vieneu_runtime = {
                        "state": "failed",
                        "stage": "Không nạp được VieNeu-TTS",
                        "error": str(exc),
                    }
                raise VideoAiDependencyError(
                    f"Không nạp được VieNeu-TTS: {exc}"
                ) from exc
            self._vieneu_engine = engine
            with self._lock:
                self._vieneu_runtime = {
                    "state": "ready",
                    "stage": "VieNeu-TTS đã sẵn sàng trên CPU",
                    "error": "",
                }
            return engine

    def list_projects(self, *, limit: int = 20) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        with self._lock:
            for path in self.project_root.glob("*.json"):
                try:
                    project = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(project, dict):
                        project["dubbing_options"] = _normalized_dubbing(
                            project.get("dubbing_options")
                        )
                        projects.append(project)
                except (OSError, ValueError, TypeError):
                    continue
        projects.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return projects[: max(1, min(100, int(limit)))]

    def create_project(self, source_path: str) -> dict[str, Any]:
        source = Path(source_path).expanduser().resolve()
        try:
            media = self.video_validator(str(source), require_audio=False)
        except (InvalidVideoFileError, FFprobeNotFoundError) as exc:
            raise VideoAiError(str(exc)) from exc
        project_id = uuid.uuid4().hex
        now = _utc_now()
        project = {
            "id": project_id,
            "source_path": str(source),
            "output_path": "",
            "media": media,
            "status": "ready",
            "stage": "Video đã sẵn sàng",
            "progress": 0,
            "error": "",
            "source_language": "auto",
            "detected_language": "",
            "target_language": "vi",
            "model_name": "small",
            "subtitles": [],
            "subtitle_detection": {
                "detected": False,
                "confidence": 0,
                "sampled_frames": 0,
                "candidate_frames": 0,
                "region": None,
                "detected_at": "",
            },
            "dubbing_options": _normalized_dubbing(None),
            "render_options": {
                "track": "translated",
                "blur": _normalized_blur({"enabled": True}),
                "style": {
                    "font_name": "Arial",
                    "font_size": 42,
                    "margin_v": 54,
                    "outline": 2,
                    "primary_color": "&H00FFFFFF",
                },
            },
            "created_at": now,
            "updated_at": now,
        }
        self._save(project)
        return project

    def get_project(self, project_id: str) -> dict[str, Any]:
        path = self._project_path(project_id)
        with self._lock:
            try:
                project = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(project, dict):
                    raise TypeError("Project payload must be an object")
                project["dubbing_options"] = _normalized_dubbing(
                    project.get("dubbing_options")
                )
                return project
            except FileNotFoundError as exc:
                raise VideoAiNotFoundError("Không tìm thấy dự án xử lý video.") from exc
            except (OSError, ValueError, TypeError) as exc:
                raise VideoAiError("Dự án xử lý video bị lỗi dữ liệu.") from exc

    def save_subtitles(
        self,
        project_id: str,
        subtitles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalized = _normalized_subtitles(subtitles)

        def update(project: dict[str, Any]) -> None:
            self._ensure_idle(project)
            if normalized != (project.get("subtitles") or []):
                _clear_generated_dubbing(project)
            project["subtitles"] = normalized
            project["stage"] = f"Đã lưu {len(normalized)} dòng phụ đề"
            project["error"] = ""

        return self._update(project_id, update)

    def save_editor_state(
        self,
        project_id: str,
        *,
        subtitles: list[dict[str, Any]],
        blur: dict[str, Any] | None = None,
        style: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_subtitles = _normalized_subtitles(subtitles)
        normalized_blur = _normalized_blur(blur)
        normalized_style = _normalized_style(style)

        def update(project: dict[str, Any]) -> None:
            self._ensure_idle(project)
            current_options = project.get("render_options") or {}
            track = str(current_options.get("track") or "translated")
            if track not in SUPPORTED_SUBTITLE_TRACKS:
                track = "translated"
            if normalized_subtitles != (project.get("subtitles") or []):
                _clear_generated_dubbing(project)
            project["subtitles"] = normalized_subtitles
            project["render_options"] = {
                "track": track,
                "blur": normalized_blur,
                "style": normalized_style,
            }
            project["stage"] = "Đã tự lưu chỉnh sửa"
            project["error"] = ""

        return self._update(project_id, update)

    def start_subtitle_detection(
        self,
        project_id: str,
        *,
        sample_count: int = 24,
    ) -> dict[str, Any]:
        if (
            importlib.util.find_spec("cv2") is None
            or importlib.util.find_spec("numpy") is None
        ):
            raise VideoAiDependencyError(
                "Chưa cài OpenCV và NumPy. Hãy cài requirements-video-ai.txt rồi khởi động lại Dyna."
            )
        requested_samples = max(8, min(48, int(sample_count)))

        def prepare(project: dict[str, Any]) -> None:
            self._ensure_idle(project)
            project.update(
                {
                    "status": "detecting",
                    "stage": "Đang chuẩn bị tìm vùng phụ đề cứng",
                    "progress": 1,
                    "error": "",
                }
            )
            detection = dict(project.get("subtitle_detection") or {})
            detection["requested_samples"] = requested_samples
            project["subtitle_detection"] = detection

        project = self._update(project_id, prepare)
        self._spawn(project_id, "subtitle-detection", self._run_subtitle_detection)
        return project

    def start_transcription(
        self,
        project_id: str,
        *,
        source_language: str = "auto",
        model_name: str = "small",
    ) -> dict[str, Any]:
        language = str(source_language or "auto").strip().lower()[:32]
        model = str(model_name or "small").strip().lower()
        if model not in SUPPORTED_MODELS:
            raise VideoAiError("Model nhận dạng không được hỗ trợ.")
        if importlib.util.find_spec("faster_whisper") is None:
            raise VideoAiDependencyError(
                "Chưa cài faster-whisper. Hãy cài requirements-video-ai.txt rồi khởi động lại Dyna."
            )

        def prepare(project: dict[str, Any]) -> None:
            self._ensure_idle(project)
            if not bool((project.get("media") or {}).get("has_audio")):
                raise VideoAiError("Video không có luồng âm thanh để nhận dạng lời nói.")
            project.update(
                {
                    "status": "transcribing",
                    "stage": "Đang chuẩn bị model nhận dạng",
                    "progress": 1,
                    "error": "",
                    "source_language": language,
                    "model_name": model,
                }
            )

        project = self._update(project_id, prepare)
        self._spawn(project_id, "transcription", self._run_transcription)
        return project

    def start_translation(
        self,
        project_id: str,
        *,
        target_language: str,
    ) -> dict[str, Any]:
        target = str(target_language or "").strip().lower()[:32]
        if not target:
            raise VideoAiError("Hãy chọn ngôn ngữ cần dịch.")
        if self.translator is None or not hasattr(self.translator, "translate_subtitles"):
            raise VideoAiDependencyError("Dịch phụ đề bằng DynaAI chưa sẵn sàng.")

        def prepare(project: dict[str, Any]) -> None:
            self._ensure_idle(project)
            if not project.get("subtitles"):
                raise VideoAiError("Chưa có phụ đề nguồn để dịch.")
            project.update(
                {
                    "status": "translating",
                    "stage": "Đang chuẩn bị dịch phụ đề",
                    "progress": 1,
                    "error": "",
                    "target_language": target,
                }
            )

        project = self._update(project_id, prepare)
        self._spawn(project_id, "translation", self._run_translation)
        return project

    def start_dubbing(
        self,
        project_id: str,
        *,
        provider: str = "edge",
        voice: str = "vi-VN-HoaiMyNeural",
        style: str = "tu_nhien",
        rate: int = 0,
        volume: int = 100,
        original_volume: int = 18,
    ) -> dict[str, Any]:
        requested = _normalized_dubbing(
            {
                "enabled": True,
                "provider": provider,
                "voice": voice,
                "style": style,
                "rate": rate,
                "volume": volume,
                "original_volume": original_volume,
            }
        )
        dependency = "vieneu" if requested["provider"] == "vieneu" else "edge_tts"
        if importlib.util.find_spec(dependency) is None:
            engine_name = "VieNeu-TTS" if requested["provider"] == "vieneu" else "Edge-TTS"
            raise VideoAiDependencyError(
                f"Chưa cài {engine_name}. Hãy cài requirements-video-ai.txt rồi khởi động lại Dyna."
            )

        def prepare(project: dict[str, Any]) -> None:
            self._ensure_idle(project)
            lines = [
                item
                for item in project.get("subtitles") or []
                if _clean_subtitle_text(item.get("translated_text"))
            ]
            if not lines:
                raise VideoAiError("Chưa có phụ đề dịch để tạo giọng lồng tiếng.")
            previous = _normalized_dubbing(project.get("dubbing_options"))
            requested["audio_path"] = previous["audio_path"]
            requested["generated_at"] = previous["generated_at"]
            requested["line_count"] = previous["line_count"]
            project.update(
                {
                    "status": "dubbing",
                    "stage": "Đang chuẩn bị giọng lồng tiếng",
                    "progress": 1,
                    "error": "",
                    "dubbing_options": dict(requested),
                }
            )

        project = self._update(project_id, prepare)
        self._spawn(project_id, "dubbing", self._run_dubbing)
        return project

    def start_render(
        self,
        project_id: str,
        *,
        track: str = "translated",
        blur: dict[str, Any] | None = None,
        style: dict[str, Any] | None = None,
        dubbing: dict[str, Any] | None = None,
        output_path: str = "",
    ) -> dict[str, Any]:
        subtitle_track = str(track or "translated").strip().lower()
        if subtitle_track not in SUPPORTED_SUBTITLE_TRACKS:
            raise VideoAiError("Loại phụ đề xuất video không hợp lệ.")
        normalized_blur = _normalized_blur(blur)
        normalized_style = _normalized_style(style)
        requested_dubbing = _normalized_dubbing(dubbing)
        requested_output = str(output_path or "").strip()

        def prepare(project: dict[str, Any]) -> None:
            self._ensure_idle(project)
            subtitles = project.get("subtitles") or []
            if not any(_subtitle_track_text(item, subtitle_track) for item in subtitles):
                raise VideoAiError("Không có nội dung phụ đề để chèn vào video.")
            current_dubbing = _normalized_dubbing(project.get("dubbing_options"))
            if requested_dubbing["enabled"]:
                if (
                    requested_dubbing["provider"] != current_dubbing["provider"]
                    or requested_dubbing["voice"] != current_dubbing["voice"]
                    or requested_dubbing["style"] != current_dubbing["style"]
                    or requested_dubbing["rate"] != current_dubbing["rate"]
                ):
                    raise VideoAiError(
                        "Công cụ, giọng, phong cách hoặc tốc độ đã thay đổi. Hãy tạo lại track lồng tiếng trước khi xuất."
                    )
                audio_path = Path(current_dubbing["audio_path"])
                if not audio_path.is_file():
                    raise VideoAiError(
                        "Chưa có track lồng tiếng. Hãy bấm Tạo lồng tiếng trước khi xuất."
                    )
            current_dubbing["enabled"] = requested_dubbing["enabled"]
            current_dubbing["volume"] = requested_dubbing["volume"]
            current_dubbing["original_volume"] = requested_dubbing["original_volume"]
            project["dubbing_options"] = current_dubbing
            project["status"] = "rendering"
            project["stage"] = "Đang chuẩn bị xuất video"
            project["progress"] = 1
            project["error"] = ""
            project["render_options"] = {
                "track": subtitle_track,
                "blur": normalized_blur,
                "style": normalized_style,
            }
            if requested_output:
                target = Path(requested_output).expanduser().resolve()
                if target == Path(project["source_path"]).resolve():
                    raise VideoAiError("File đầu ra không được trùng với video nguồn.")
                project["output_path"] = str(target)
            else:
                project["output_path"] = str(self._next_output_path(project, subtitle_track))

        project = self._update(project_id, prepare)
        self._spawn(project_id, "render", self._run_render)
        return project

    def cancel(self, project_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        if project.get("status") not in BUSY_STATUSES:
            return project
        with self._lock:
            event = self._cancel_events.get(project_id)
            if event is not None:
                event.set()

        def update(current: dict[str, Any]) -> None:
            if current.get("status") in BUSY_STATUSES:
                current["stage"] = "Đang dừng tác vụ..."

        return self._update(project_id, update)

    def _run_subtitle_detection(
        self,
        project_id: str,
        cancel_event: threading.Event,
    ) -> None:
        import cv2

        project = self.get_project(project_id)
        source = Path(project["source_path"])
        detection = project.get("subtitle_detection") or {}
        sample_count = max(8, min(48, int(detection.get("requested_samples") or 24)))
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            capture.release()
            raise VideoAiError("Không mở được video để tìm vùng phụ đề cứng.")
        try:
            media = project.get("media") or {}
            duration = max(0.0, _finite_number(media.get("duration_seconds")))
            if duration <= 0:
                frame_count = max(0.0, capture.get(cv2.CAP_PROP_FRAME_COUNT))
                fps = max(0.0, capture.get(cv2.CAP_PROP_FPS))
                if frame_count > 0 and fps > 0:
                    duration = frame_count / fps
            if duration <= 0:
                raise VideoAiError("Không đọc được thời lượng video để tìm phụ đề.")

            effective_samples = min(
                sample_count,
                max(8, int(math.ceil(duration * 2))),
            )
            timestamps = [
                duration * (index + 1) / (effective_samples + 1)
                for index in range(effective_samples)
            ]
            frames: list[Any] = []
            for index, timestamp in enumerate(timestamps, start=1):
                if cancel_event.is_set():
                    raise VideoAiCancelled()
                capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
                success, frame = capture.read()
                if success and frame is not None:
                    frames.append(frame)
                progress = 5 + int(index / max(1, len(timestamps)) * 76)
                self._set_progress(
                    project_id,
                    progress,
                    f"Đang phân tích khung hình {index}/{len(timestamps)}",
                )
        finally:
            capture.release()

        if cancel_event.is_set():
            raise VideoAiCancelled()
        self._set_progress(project_id, 86, "Đang tổng hợp vị trí chữ lặp lại")
        result = _detect_subtitle_region_from_frames(frames)
        result["detected_at"] = _utc_now()
        if cancel_event.is_set():
            raise VideoAiCancelled()

        def finish(current: dict[str, Any]) -> None:
            current["status"] = "ready"
            current["progress"] = 100
            current["error"] = ""
            current["subtitle_detection"] = result
            if result["detected"] and result["region"]:
                current_options = current.get("render_options") or {}
                previous_blur = current_options.get("blur") or {}
                detected_blur = dict(result["region"])
                detected_blur["strength"] = int(previous_blur.get("strength") or 22)
                detected_blur = _normalized_blur(detected_blur)
                current["render_options"] = {
                    "track": current_options.get("track") or "translated",
                    "blur": detected_blur,
                    "style": current_options.get("style") or _normalized_style(None),
                }
                current["subtitle_detection"]["region"] = detected_blur
                current["stage"] = (
                    f"Đã tìm thấy vùng phụ đề · tin cậy {result['confidence']}%"
                )
            else:
                current["stage"] = (
                    "Chưa tìm thấy vùng phụ đề rõ ràng · hãy chỉnh khung thủ công"
                )

        self._update(project_id, finish)

    def _run_transcription(self, project_id: str, cancel_event: threading.Event) -> None:
        _configure_cuda_dll_search()
        from faster_whisper import WhisperModel

        project = self.get_project(project_id)
        model_name = project["model_name"]
        model_root = self.project_root.parent / "video-ai-models" / "faster-whisper"
        model_root.mkdir(parents=True, exist_ok=True)
        cuda = _cuda_runtime_status()
        device = "cuda" if cuda["ready"] else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        device_label = "GPU CUDA" if device == "cuda" else "CPU"
        self._set_progress(
            project_id,
            3,
            f"Đang tải hoặc nạp model {model_name} trên {device_label}",
        )
        if cancel_event.is_set():
            raise VideoAiCancelled()
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            download_root=str(model_root),
        )
        if cancel_event.is_set():
            raise VideoAiCancelled()
        self._set_progress(project_id, 8, "Đang nghe và tạo phụ đề")
        language = project.get("source_language")
        segments, info = model.transcribe(
            project["source_path"],
            language=None if language == "auto" else language,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=True,
        )
        duration = max(
            0.1,
            _finite_number(getattr(info, "duration", 0)),
            _finite_number((project.get("media") or {}).get("duration_seconds")),
        )
        subtitles: list[dict[str, Any]] = []
        last_update = 0.0
        for segment in segments:
            if cancel_event.is_set():
                raise VideoAiCancelled()
            text = _clean_subtitle_text(getattr(segment, "text", ""))
            if text:
                subtitles.append(
                    {
                        "id": str(len(subtitles) + 1),
                        "start": round(max(0.0, float(segment.start)), 3),
                        "end": round(max(float(segment.start) + 0.05, float(segment.end)), 3),
                        "text": text,
                        "translated_text": "",
                    }
                )
            now = time.monotonic()
            if now - last_update >= 0.5:
                progress = 8 + int(min(88, max(0.0, float(segment.end) / duration * 88)))
                self._set_progress(project_id, progress, f"Đã nhận dạng {len(subtitles)} dòng")
                last_update = now

        detected = str(getattr(info, "language", "") or "")

        def finish(current: dict[str, Any]) -> None:
            _clear_generated_dubbing(current)
            current.update(
                {
                    "status": "ready",
                    "stage": f"Đã tạo {len(subtitles)} dòng phụ đề",
                    "progress": 100,
                    "error": "",
                    "detected_language": detected,
                    "subtitles": subtitles,
                }
            )

        self._update(project_id, finish)

    def _run_translation(self, project_id: str, cancel_event: threading.Event) -> None:
        project = self.get_project(project_id)
        subtitles = list(project.get("subtitles") or [])
        chunks = _translation_chunks(subtitles)
        translated_by_id: dict[str, str] = {}
        processed = 0
        source_language = str(project.get("detected_language") or project.get("source_language") or "auto")
        target_language = str(project.get("target_language") or "")
        for index, chunk in enumerate(chunks, start=1):
            if cancel_event.is_set():
                raise VideoAiCancelled()
            progress = 5 + int((index - 1) / max(1, len(chunks)) * 92)
            translated = self._translate_chunk_with_retry(
                project_id,
                chunk,
                source_language=source_language,
                target_language=target_language,
                progress=progress,
                cancel_event=cancel_event,
            )
            for item in translated:
                translated_by_id[str(item.get("id") or "")] = _clean_subtitle_text(item.get("text"))
            processed += len(chunk)
            progress = 5 + int(index / max(1, len(chunks)) * 92)
            self._set_progress(
                project_id,
                progress,
                f"Đã dịch {min(processed, len(subtitles))}/{len(subtitles)} dòng",
            )

        def finish(current: dict[str, Any]) -> None:
            _clear_generated_dubbing(current)
            for item in current.get("subtitles") or []:
                item["translated_text"] = translated_by_id.get(str(item.get("id") or ""), "")
            current.update(
                {
                    "status": "ready",
                    "stage": f"Đã dịch {len(translated_by_id)} dòng phụ đề",
                    "progress": 100,
                    "error": "",
                }
            )

        self._update(project_id, finish)

    def _run_dubbing(self, project_id: str, cancel_event: threading.Event) -> None:
        import numpy as np

        project = self.get_project(project_id)
        options = _normalized_dubbing(project.get("dubbing_options"))
        entries = [
            {
                "id": str(item.get("id") or index),
                "start": max(0.0, _finite_number(item.get("start"))),
                "end": max(
                    _finite_number(item.get("start")) + 0.05,
                    _finite_number(item.get("end")),
                ),
                "text": _clean_subtitle_text(item.get("translated_text")),
            }
            for index, item in enumerate(project.get("subtitles") or [], start=1)
            if _clean_subtitle_text(item.get("translated_text"))
        ]
        if not entries:
            raise VideoAiError("Chưa có phụ đề dịch để tạo giọng lồng tiếng.")
        generation_id = uuid.uuid4().hex[:12]
        generation_root = self.scratch_root / project_id / "dubbing" / generation_id
        generation_root.mkdir(parents=True, exist_ok=True)
        rate_text = f"{options['rate']:+d}%"

        def report_generated(index: int) -> None:
            progress = 4 + int(index / max(1, len(entries)) * 64)
            self._set_progress(
                project_id,
                progress,
                f"Đã tạo giọng {index}/{len(entries)} dòng",
            )

        async def synthesize_edge() -> list[Path]:
            import edge_tts

            generated: list[Path] = []
            for index, entry in enumerate(entries, start=1):
                if cancel_event.is_set():
                    raise VideoAiCancelled()
                target = generation_root / f"{index:05d}.mp3"
                last_error: Exception | None = None
                for attempt in range(3):
                    try:
                        communicator = edge_tts.Communicate(
                            entry["text"],
                            options["voice"],
                            rate=rate_text,
                        )
                        await communicator.save(str(target))
                        if target.is_file() and target.stat().st_size > 128:
                            last_error = None
                            break
                    except Exception as exc:
                        last_error = exc
                    if cancel_event.is_set():
                        raise VideoAiCancelled()
                    await asyncio.sleep(1.0 + attempt)
                if last_error is not None or not target.is_file():
                    raise VideoAiError(
                        f"Không tạo được giọng cho dòng {index}: {last_error or 'không nhận được âm thanh'}"
                    )
                generated.append(target)
                report_generated(index)
            return generated

        def synthesize_vieneu() -> list[Path]:
            self._set_progress(
                project_id,
                3,
                "Đang nạp VieNeu-TTS trên CPU · lần đầu có thể tải khoảng 300 MB",
            )
            engine = self._ensure_vieneu_engine()

            generated: list[Path] = []
            batch_size = 16
            for batch_start in range(0, len(entries), batch_size):
                if cancel_event.is_set():
                    raise VideoAiCancelled()
                batch = entries[batch_start:batch_start + batch_size]
                self._set_progress(
                    project_id,
                    4 + int(batch_start / max(1, len(entries)) * 60),
                    f"Đang tạo batch giọng {batch_start + 1}–{batch_start + len(batch)}/{len(entries)}",
                )
                try:
                    with self._vieneu_lock:
                        audios = engine.infer_batch(
                            [entry["text"] for entry in batch],
                            voice=options["voice"],
                            style=options["style"],
                            batch_size=batch_size,
                        )
                except Exception as exc:
                    raise VideoAiError(
                        f"VieNeu-TTS không tạo được batch từ dòng {batch_start + 1}: {exc}"
                    ) from exc
                if len(audios) != len(batch):
                    raise VideoAiError(
                        "VieNeu-TTS trả về thiếu câu trong batch lồng tiếng."
                    )
                for offset, audio in enumerate(audios):
                    index = batch_start + offset + 1
                    target = generation_root / f"{index:05d}.wav"
                    with self._vieneu_lock:
                        engine.save(audio, str(target))
                    if not target.is_file() or target.stat().st_size <= 128:
                        raise VideoAiError(
                            f"VieNeu-TTS không nhận được âm thanh cho dòng {index}."
                        )
                    generated.append(target)
                    report_generated(index)
            return generated

        generated_files = (
            synthesize_vieneu()
            if options["provider"] == "vieneu"
            else asyncio.run(synthesize_edge())
        )
        if cancel_event.is_set():
            raise VideoAiCancelled()

        ffmpeg = self._resolve_ffmpeg()
        ffprobe = resolve_ffprobe()
        media_duration = max(
            0.1,
            _finite_number((project.get("media") or {}).get("duration_seconds")),
            max(entry["end"] for entry in entries),
        )
        sample_rate = 44_100
        total_samples = max(1, int(math.ceil(media_duration * sample_rate)))
        raw_mix_path = generation_root / "mix.i32"
        mix = np.memmap(
            raw_mix_path,
            dtype=np.int32,
            mode="w+",
            shape=(total_samples,),
        )
        mix[:] = 0
        speed_warnings: list[dict[str, Any]] = []
        max_speed = 1.0

        for index, (entry, source_audio) in enumerate(
            zip(entries, generated_files),
            start=1,
        ):
            if cancel_event.is_set():
                del mix
                raise VideoAiCancelled()
            slot_duration = max(0.05, entry["end"] - entry["start"])
            speech_duration = _probe_audio_duration(ffprobe, source_audio)
            minimum_speed = max(0.5, 1.0 + options["rate"] / 100.0)
            if options["provider"] == "edge":
                minimum_speed = 1.0
            speed = (
                max(minimum_speed, speech_duration / slot_duration)
                if speech_duration > 0
                else minimum_speed
            )
            max_speed = max(max_speed, speed)
            if speed >= 1.35:
                speed_warnings.append(
                    {
                        "id": entry["id"],
                        "speed": round(speed, 2),
                        "slot_duration": round(slot_duration, 3),
                        "speech_duration": round(speech_duration, 3),
                    }
                )
            adjusted = generation_root / f"{index:05d}-aligned.wav"
            filters = _atempo_filters(speed)
            filters.extend(
                [
                    f"apad=pad_dur={slot_duration:.6f}",
                    f"atrim=duration={slot_duration:.6f}",
                    "aresample=44100",
                    "aformat=sample_fmts=s16:channel_layouts=mono",
                ]
            )
            completed = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source_audio),
                    "-af",
                    ",".join(filters),
                    "-ac",
                    "1",
                    "-ar",
                    str(sample_rate),
                    "-c:a",
                    "pcm_s16le",
                    str(adjusted),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(30, int(slot_duration * 10)),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if completed.returncode != 0 or not adjusted.is_file():
                del mix
                detail = (completed.stderr or completed.stdout or "").strip()
                raise VideoAiError(
                    f"Không căn được thời lượng giọng dòng {index}: {detail[-1000:]}"
                )
            with wave.open(str(adjusted), "rb") as reader:
                samples = np.frombuffer(
                    reader.readframes(reader.getnframes()),
                    dtype="<i2",
                ).astype(np.int32)
            start_sample = max(0, int(round(entry["start"] * sample_rate)))
            available = max(0, min(len(samples), total_samples - start_sample))
            if available:
                mix[start_sample:start_sample + available] += samples[:available]
            progress = 70 + int(index / max(1, len(entries)) * 24)
            self._set_progress(
                project_id,
                progress,
                f"Đang căn thời gian giọng {index}/{len(entries)}",
            )

        dubbed_path = generation_root / "dubbed.wav"
        with wave.open(str(dubbed_path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(sample_rate)
            chunk_size = sample_rate * 10
            for start in range(0, total_samples, chunk_size):
                if cancel_event.is_set():
                    del mix
                    raise VideoAiCancelled()
                chunk = np.clip(
                    mix[start:min(total_samples, start + chunk_size)],
                    -32768,
                    32767,
                ).astype("<i2")
                writer.writeframes(chunk.tobytes())
        mix.flush()
        del mix
        raw_mix_path.unlink(missing_ok=True)
        if not dubbed_path.is_file() or dubbed_path.stat().st_size < 128:
            raise VideoAiError("Không tạo được track lồng tiếng hoàn chỉnh.")

        def finish(current: dict[str, Any]) -> None:
            current_options = _normalized_dubbing(current.get("dubbing_options"))
            current_options.update(
                {
                    "enabled": True,
                    "audio_path": str(dubbed_path),
                    "generated_at": _utc_now(),
                    "line_count": len(entries),
                    "max_speed": round(max_speed, 2),
                    "speed_warnings": speed_warnings,
                }
            )
            warning_suffix = (
                f" · {len(speed_warnings)} dòng bị ép tốc độ"
                if speed_warnings
                else ""
            )
            current.update(
                {
                    "status": "ready",
                    "stage": f"Đã tạo giọng lồng tiếng cho {len(entries)} dòng{warning_suffix}",
                    "progress": 100,
                    "error": "",
                    "dubbing_options": current_options,
                }
            )

        self._update(project_id, finish)

    def _translate_chunk_with_retry(
        self,
        project_id: str,
        chunk: list[dict[str, Any]],
        *,
        source_language: str,
        target_language: str,
        progress: int,
        cancel_event: threading.Event,
    ) -> list[dict[str, str]]:
        payload = [
            {"id": str(item.get("id") or ""), "text": _clean_subtitle_text(item.get("text"))}
            for item in chunk
        ]
        rate_limit_retries = 0
        while True:
            if cancel_event.is_set():
                raise VideoAiCancelled()
            try:
                return self.translator.translate_subtitles(
                    payload,
                    source_language=source_language,
                    target_language=target_language,
                )
            except Exception as exc:
                if getattr(exc, "status_code", None) != 429 or rate_limit_retries >= 3:
                    raise
                rate_limit_retries += 1
                try:
                    delay = int(getattr(exc, "retry_after", None) or 60)
                except (TypeError, ValueError):
                    delay = 60
                remaining = max(1, min(120, delay)) + 1
                while remaining > 0:
                    self._set_progress(
                        project_id,
                        progress,
                        f"DynaAI đang giới hạn tốc độ. Tự tiếp tục sau {remaining} giây",
                    )
                    interval = min(1, remaining)
                    if self._rate_limit_waiter(cancel_event, interval):
                        raise VideoAiCancelled() from exc
                    remaining -= interval

    def _run_render(self, project_id: str, cancel_event: threading.Event) -> None:
        project = self.get_project(project_id)
        source = Path(project["source_path"])
        target = Path(project["output_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        scratch = self.scratch_root / project_id
        scratch.mkdir(parents=True, exist_ok=True)
        track = project["render_options"]["track"]
        ass_path = scratch / f"{track}.ass"
        media = project.get("media") or {}
        count = _write_ass(
            ass_path,
            project.get("subtitles") or [],
            track,
            width=int(media.get("width") or 1920),
            height=int(media.get("height") or 1080),
            style=project["render_options"]["style"],
            subtitle_region=project["render_options"]["blur"],
        )
        if count < 1:
            raise VideoAiError("Không có nội dung phụ đề hợp lệ để xuất.")
        filter_graph = _build_filter_graph(ass_path, project["render_options"]["blur"])
        dubbing = _normalized_dubbing(project.get("dubbing_options"))
        dubbed_path = Path(dubbing["audio_path"]) if dubbing["audio_path"] else None
        use_dubbing = bool(
            dubbing["enabled"]
            and dubbed_path is not None
            and dubbed_path.is_file()
        )
        command = [
            self._resolve_ffmpeg(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
        ]
        if use_dubbing and dubbed_path is not None:
            command.extend(["-i", str(dubbed_path)])
            dubbed_volume = dubbing["volume"] / 100.0
            if bool(media.get("has_audio")):
                original_volume = dubbing["original_volume"] / 100.0
                filter_graph += (
                    f";[0:a]volume={original_volume:.3f}[original_audio];"
                    f"[1:a]volume={dubbed_volume:.3f}[dubbed_audio];"
                    "[original_audio][dubbed_audio]"
                    "amix=inputs=2:duration=longest:dropout_transition=0[aout]"
                )
            else:
                filter_graph += f";[1:a]volume={dubbed_volume:.3f}[aout]"
        command.extend([
            "-filter_complex",
            filter_graph,
            "-map",
            "[vout]",
        ])
        if use_dubbing:
            command.extend(["-map", "[aout]"])
        else:
            command.extend(["-map", "0:a?"])
        command.extend([
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-nostats",
            str(target),
        ])
        duration = max(0.1, _finite_number(media.get("duration_seconds"), default=1.0))
        last_update = 0.0
        output_lines: list[str] = []
        try:
            with workload_slot(
                "ffmpeg",
                profile_id="video-ai",
                video_id=project_id,
                platform="desktop",
                priority=50,
                cancel_event=cancel_event,
            ):
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(source.parent),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                assert process.stdout is not None
                try:
                    for raw_line in process.stdout:
                        line = raw_line.strip()
                        if line:
                            output_lines.append(line)
                            output_lines = output_lines[-100:]
                        if cancel_event.is_set():
                            process.terminate()
                            try:
                                process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                process.kill()
                            raise VideoAiCancelled()
                        if line.startswith(("out_time_us=", "out_time_ms=")):
                            elapsed = _finite_number(line.split("=", 1)[1]) / 1_000_000
                            now = time.monotonic()
                            if now - last_update >= 0.5:
                                progress = 3 + int(min(94.0, elapsed / duration * 94.0))
                                self._set_progress(project_id, progress, "Đang mã hóa video và chèn phụ đề")
                                last_update = now
                finally:
                    process.stdout.close()
                return_code = process.wait()
        except WorkloadCancelled as exc:
            raise VideoAiCancelled() from exc

        if return_code != 0 or not target.is_file() or target.stat().st_size < 1024:
            target.unlink(missing_ok=True)
            diagnostic_lines = [
                line
                for line in output_lines
                if not re.fullmatch(
                    r"(?:frame|fps|stream_\d+_\d+_q|bitrate|total_size|"
                    r"out_time_us|out_time_ms|out_time|dup_frames|drop_frames|speed|progress)=.*",
                    line,
                )
            ]
            detail = "\n".join(diagnostic_lines[-12:] or output_lines[-8:])
            if not detail:
                detail = "FFmpeg không tạo được video đầu ra."
            raise VideoAiError(f"Xuất video thất bại: {detail}")

        def finish(current: dict[str, Any]) -> None:
            current.update(
                {
                    "status": "completed",
                    "stage": "Đã xuất video",
                    "progress": 100,
                    "error": "",
                    "output_path": str(target),
                    "subtitle_path": "",
                }
            )

        self._update(project_id, finish)

    def _spawn(
        self,
        project_id: str,
        name: str,
        runner: Callable[[str, threading.Event], None],
    ) -> None:
        cancel_event = threading.Event()

        def run() -> None:
            try:
                runner(project_id, cancel_event)
            except VideoAiCancelled:
                self._mark_cancelled(project_id)
            except Exception as exc:
                logger.exception("[Video AI] Tác vụ %s thất bại cho dự án %s", name, project_id)
                self._mark_failed(project_id, str(exc))
            finally:
                with self._lock:
                    self._cancel_events.pop(project_id, None)
                    self._threads.pop(project_id, None)

        thread = threading.Thread(
            target=run,
            daemon=True,
            name=f"video-ai-{name}-{project_id[:8]}",
        )
        with self._lock:
            self._cancel_events[project_id] = cancel_event
            self._threads[project_id] = thread
        thread.start()

    def _mark_cancelled(self, project_id: str) -> None:
        def update(project: dict[str, Any]) -> None:
            project.update(
                {
                    "status": "cancelled",
                    "stage": "Tác vụ đã dừng",
                    "error": "",
                }
            )

        self._update(project_id, update)

    def _mark_failed(self, project_id: str, message: str) -> None:
        def update(project: dict[str, Any]) -> None:
            project.update(
                {
                    "status": "failed",
                    "stage": "Xử lý thất bại",
                    "error": str(message or "Lỗi không xác định")[:8_000],
                }
            )

        self._update(project_id, update)

    def _set_progress(self, project_id: str, progress: int, stage: str) -> None:
        def update(project: dict[str, Any]) -> None:
            project["progress"] = max(0, min(100, int(progress)))
            project["stage"] = str(stage or "")[:500]

        self._update(project_id, update)

    def _ensure_idle(self, project: dict[str, Any]) -> None:
        if project.get("status") in BUSY_STATUSES:
            raise VideoAiBusyError("Dự án đang có tác vụ chạy. Hãy chờ hoặc dừng tác vụ hiện tại.")

    def _next_output_path(self, project: dict[str, Any], track: str) -> Path:
        source = Path(project["source_path"])
        language = project.get("target_language") if track == "translated" else project.get("source_language")
        safe_language = re.sub(r"[^A-Za-z0-9_-]+", "-", str(language or track)).strip("-") or track
        candidate = source.with_name(f"{source.stem}_dyna_{safe_language}.mp4")
        index = 2
        while candidate.exists():
            candidate = source.with_name(f"{source.stem}_dyna_{safe_language}_{index}.mp4")
            index += 1
        return candidate

    def _resolve_ffmpeg(self) -> str:
        for candidate in (shutil.which("ffmpeg"), "C:/ffmpeg/bin/ffmpeg.exe"):
            if candidate and Path(candidate).is_file():
                return str(candidate)
        raise VideoAiDependencyError(
            "Không tìm thấy FFmpeg trong PATH hoặc tại C:/ffmpeg/bin/ffmpeg.exe."
        )

    def _project_path(self, project_id: str) -> Path:
        normalized = str(project_id or "").strip().lower()
        if not PROJECT_ID_PATTERN.fullmatch(normalized):
            raise VideoAiNotFoundError("Mã dự án xử lý video không hợp lệ.")
        return self.project_root / f"{normalized}.json"

    def _save(self, project: dict[str, Any]) -> None:
        with self._lock:
            project["updated_at"] = _utc_now()
            _atomic_json_write(self._project_path(str(project["id"])), project)

    def _update(
        self,
        project_id: str,
        updater: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        with self._lock:
            project = self.get_project(project_id)
            updater(project)
            self._save(project)
            return project
