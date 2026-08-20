from __future__ import annotations

from datetime import datetime

from rich import box
from rich.panel import Panel
from rich.table import Table

from core.utils import console
from profile_automation.watchers.douyin_video import (
    DouyinVideo,
    _is_photo_aweme,
    _parse_aweme,
)


MAX_AWEME_IDS_PER_SCAN = 4


def _short_text(value, max_len: int = 54) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _format_time(timestamp: int) -> str:
    if not timestamp:
        return "?"
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(timestamp)


def _analyze_aweme_payload(data: dict) -> dict:
    aweme_list = data.get("aweme_list", []) or []
    analysis = {
        "json_keys": len(data.keys()),
        "aweme_count": len(aweme_list),
        "valid_video_count": 0,
        "top_video_count": 0,
        "image_post_count": 0,
        "private_or_deleted_count": 0,
        "invalid_duration_count": 0,
        "sample_ids": [],
    }
    for item in aweme_list:
        aweme_id = item.get("aweme_id")
        if aweme_id and len(analysis["sample_ids"]) < MAX_AWEME_IDS_PER_SCAN:
            analysis["sample_ids"].append(str(aweme_id))
        if item.get("is_top", 0) == 1:
            analysis["top_video_count"] += 1
            continue
        if _is_photo_aweme(item):
            analysis["image_post_count"] += 1
            continue
        status = item.get("status", {}) or {}
        if status.get("is_delete", 0) or status.get("private_status", 0):
            analysis["private_or_deleted_count"] += 1
            continue
        video = _parse_aweme(item)
        if not video or not video.is_valid_video():
            analysis["invalid_duration_count"] += 1
            continue
        analysis["valid_video_count"] += 1
    return analysis


def print_response_analysis(
    profile_id: str,
    page_count: int,
    response_url: str,
    data: dict,
    parsed_videos: list[DouyinVideo],
    total_videos: int,
) -> None:
    analysis = _analyze_aweme_payload(data)
    table = Table(
        title=f"Profile {profile_id} · Phân tích JSON Douyin · Trang {page_count}",
        box=box.SIMPLE_HEAVY,
        show_lines=False,
    )
    table.add_column("Hạng mục", style="cyan", no_wrap=True)
    table.add_column("Giá trị", style="white")
    table.add_column("Ghi chú", style="dim")
    table.add_row("API status", str(data.get("status_code")), "0 là response hợp lệ")
    table.add_row("aweme_list", str(analysis["aweme_count"]), "Số item thô")
    table.add_row("Video hợp lệ", str(analysis["valid_video_count"]), "Đã parse")
    table.add_row("Video ghim", str(analysis["top_video_count"]), "Bỏ qua")
    table.add_row("Ảnh/carousel", str(analysis["image_post_count"]), "Bỏ qua")
    table.add_row(
        "Riêng tư/đã xóa",
        str(analysis["private_or_deleted_count"]),
        "Bỏ qua",
    )
    table.add_row("Duration lỗi", str(analysis["invalid_duration_count"]), "Bỏ qua")
    table.add_row("has_more", str(data.get("has_more", "?")), "1 còn trang")
    table.add_row("cursor", str(data.get("max_cursor", "?")), "Trang tiếp theo")
    table.add_row("Tổng đã gom", str(total_videos), "Video hợp lệ")
    table.add_row("ID mẫu", ", ".join(analysis["sample_ids"]) or "-", "ID đầu tiên")
    console.print(Panel(table, title="📦 Gói tin bắt được", border_style="green"))
    console.print(f"[dim]URL: {_short_text(response_url, 140)}[/]")
    if parsed_videos:
        print_video_table(
            profile_id,
            parsed_videos,
            title=f"Video parse được từ trang {page_count}",
        )


def print_video_table(
    profile_id: str,
    videos: list[DouyinVideo],
    title: str | None = None,
) -> None:
    table = Table(
        title=title or f"Profile {profile_id} · Video đã lấy được",
        box=box.SIMPLE,
        show_lines=False,
    )
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("Aweme ID", style="cyan", no_wrap=True)
    table.add_column("Thời gian", style="green", no_wrap=True)
    table.add_column("Like", justify="right", style="magenta", no_wrap=True)
    table.add_column("View", justify="right", style="yellow", no_wrap=True)
    table.add_column("Giây", justify="right", no_wrap=True)
    table.add_column("Mô tả", style="white")
    shown_videos = videos[:12]
    for index, video in enumerate(shown_videos, start=1):
        table.add_row(
            str(index),
            video.aweme_id,
            _format_time(video.create_time),
            f"{video.like_count:,}",
            f"{video.play_count:,}",
            f"{video.duration_seconds:.1f}",
            _short_text(video.desc),
        )
    if len(videos) > len(shown_videos):
        table.caption = f"Đang hiển thị {len(shown_videos)}/{len(videos)} video."
    console.print(table)
