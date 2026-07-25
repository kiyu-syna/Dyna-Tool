from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import core.config as config
from core.runtime_paths import logs_dir
from core.utils import logger


_WRITE_LOCK = threading.RLock()
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.-]+")


def _segment(value: object, fallback: str) -> str:
    cleaned = _SAFE_SEGMENT.sub("_", str(value or "").strip()).strip("._")
    return cleaned[:80] or fallback


def _safe_page_url(page, fallback: str = "") -> str:
    try:
        return str(page.url or fallback)
    except Exception:
        return str(fallback or "")


def _bounded(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return str(value)[:1000]
    if isinstance(value, dict):
        return {
            str(key)[:120]: _bounded(item, depth=depth + 1)
            for key, item in list(value.items())[:80]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth=depth + 1) for item in list(value)[:80]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:8000]


def attach_response_trace(page) -> dict:
    """Keep the latest document/XHR/fetch response without retaining large bodies."""
    trace: dict[str, Any] = {}

    def capture(response) -> None:
        try:
            request = getattr(response, "request", None)
            resource_type = str(getattr(request, "resource_type", "") or "")
            status = int(getattr(response, "status", 0) or 0)
            if resource_type not in {"document", "xhr", "fetch"} and status < 400:
                return
            headers = getattr(response, "headers", {}) or {}
            content_type = str(headers.get("content-type", ""))
            item: dict[str, Any] = {
                "url": str(getattr(response, "url", "") or ""),
                "status": status,
                "resource_type": resource_type,
                "content_type": content_type,
            }
            if status >= 400 or "json" in content_type.lower():
                try:
                    item["body_preview"] = str(response.text() or "")[:4000]
                except Exception:
                    pass
            trace.clear()
            trace.update(item)
        except Exception:
            pass

    try:
        page.on("response", capture)
    except Exception:
        pass
    return trace


def record_browser_diagnostic(
    *,
    page=None,
    profile_id: str = "",
    video_id: str = "",
    platform: str = "unknown",
    error: object = "",
    url: str = "",
    last_response: dict | None = None,
    send_telegram: bool = True,
) -> dict:
    occurred_at = datetime.now()
    event_id = f"{occurred_at.strftime('%H%M%S_%f')}_{uuid.uuid4().hex[:6]}"
    directory = (
        logs_dir()
        / "diagnostics"
        / occurred_at.strftime("%Y%m%d")
        / f"profile_{_segment(profile_id, 'unknown')}"
        / f"video_{_segment(video_id, 'unknown')}"
        / _segment(platform, "unknown")
        / event_id
    )
    screenshot_path = directory / "screenshot.png"
    metadata_path = directory / "diagnostic.json"
    page_url = _safe_page_url(page, url)
    screenshot_error = ""

    with _WRITE_LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        if page is not None:
            try:
                page.screenshot(path=str(screenshot_path), full_page=True, timeout=15000)
            except Exception as exc:
                screenshot_error = str(exc)
        metadata = {
            "event_id": event_id,
            "occurred_at": occurred_at.isoformat(timespec="seconds"),
            "profile_id": str(profile_id or ""),
            "video_id": str(video_id or ""),
            "platform": str(platform or "unknown"),
            "url": page_url,
            "error": str(error or ""),
            "last_response": _bounded(last_response or {}),
            "screenshot_path": str(screenshot_path) if screenshot_path.is_file() else "",
            "screenshot_error": screenshot_error,
        }
        temporary_path = metadata_path.with_suffix(".json.tmp")
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, metadata_path)

    record = {**metadata, "metadata_path": str(metadata_path)}
    logger.info(
        "[Diagnostic] Saved profile=%s video=%s platform=%s at %s",
        profile_id or "-",
        video_id or "-",
        platform,
        metadata_path,
    )
    if send_telegram:
        try:
            from services.integrations.telegram_service import send_diagnostic_notification

            send_diagnostic_notification(record)
        except Exception as exc:
            logger.warning("Không thể gửi dữ liệu chẩn đoán lên Telegram: %s", exc)
    return record
