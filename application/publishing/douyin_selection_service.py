from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from uuid import uuid4

import requests

from application.publishing.manual_publish_service import (
    ManualPublishItem,
    ManualPublishRequest,
    ManualPublishService,
    ManualPublishTarget,
)
from core.runtime_paths import runtime_root, state_dir
from core.utils import logger
from services.publishing.video_validation_service import validate_video_file


MAX_SELECTION_VIDEOS = 50
SELECTION_RETENTION = timedelta(days=7)
ACTIVE_STATUSES = {"selecting", "ready", "preparing"}
TERMINAL_STATUSES = {"published", "cancelled", "expired"}
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{5,128}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def _parse_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class DouyinSelectionService:
    """Persist browser selections and prepare them for Publish Center."""

    def __init__(
        self,
        manual_publish: ManualPublishService,
        *,
        state_file: str | Path | None = None,
        media_root: str | Path | None = None,
        downloader: Callable[[dict[str, Any], Path], Path] | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.manual_publish = manual_publish
        self.state_file = Path(
            state_file or state_dir() / "douyin-selection-sessions.json"
        )
        self.media_root = Path(media_root or runtime_root() / "douyin-selections")
        self._downloader = downloader or self._download_direct
        self._now = now
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._data = self._load()
        self._recover_interrupted_sessions()
        self._expire_old_sessions()

    @staticmethod
    def _validated_profile_url(value: str) -> str:
        raw = str(value or "").strip()
        try:
            parsed = urlparse(raw)
        except ValueError as exc:
            raise ValueError("Link trang cá nhân Douyin không hợp lệ.") from exc
        hostname = str(parsed.hostname or "").casefold()
        if parsed.scheme not in {"http", "https"} or not (
            hostname == "douyin.com" or hostname.endswith(".douyin.com")
        ):
            raise ValueError("Link phải thuộc tên miền douyin.com.")
        if not parsed.path.startswith("/user/"):
            raise ValueError("Hãy nhập link trang cá nhân Douyin có dạng /user/....")
        return raw

    @staticmethod
    def _validated_source_url(value: object, video_id: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return f"https://www.douyin.com/video/{video_id}"
        parsed = urlparse(raw)
        hostname = str(parsed.hostname or "").casefold()
        if parsed.scheme not in {"http", "https"} or not (
            hostname == "douyin.com" or hostname.endswith(".douyin.com")
        ):
            return f"https://www.douyin.com/video/{video_id}"
        return raw

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"sessions": {}}
        sessions = payload.get("sessions")
        return {"sessions": sessions if isinstance(sessions, dict) else {}}

    def _save_locked(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self._data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.state_file)

    def _recover_interrupted_sessions(self) -> None:
        changed = False
        with self._lock:
            for session in self._data["sessions"].values():
                if session.get("status") != "preparing":
                    continue
                session["status"] = "ready"
                session["last_error"] = (
                    "Dyna đã khởi động lại khi đang chuẩn bị video. "
                    "Hãy xác nhận lại để tiếp tục."
                )
                session["updated_at"] = _iso(self._now())
                changed = True
            if changed:
                self._save_locked()

    def _expire_old_sessions(self) -> None:
        cutoff = self._now().astimezone(timezone.utc) - SELECTION_RETENTION
        changed = False
        with self._lock:
            for session in self._data["sessions"].values():
                if session.get("status") in TERMINAL_STATUSES:
                    continue
                updated_at = _parse_datetime(session.get("updated_at"))
                if updated_at and updated_at < cutoff:
                    session["status"] = "expired"
                    session["updated_at"] = _iso(self._now())
                    changed = True
            if changed:
                self._save_locked()

    def create(self, source_url: str) -> dict[str, Any]:
        source_url = self._validated_profile_url(source_url)
        now = self._now()
        session_id = uuid4().hex
        session = {
            "id": session_id,
            "source_url": source_url,
            "status": "selecting",
            "items": [],
            "selected_count": 0,
            "focus_requested": False,
            "created_at": _iso(now),
            "updated_at": _iso(now),
            "expires_at": _iso(now + SELECTION_RETENTION),
            "last_error": "",
        }
        with self._lock:
            for current in self._data["sessions"].values():
                if current.get("status") == "selecting":
                    current["status"] = "cancelled"
                    current["updated_at"] = _iso(now)
            self._data["sessions"][session_id] = session
            self._save_locked()
        return deepcopy(session)

    def get(self, session_id: str) -> dict[str, Any] | None:
        self._expire_old_sessions()
        with self._lock:
            session = self._data["sessions"].get(str(session_id))
            return deepcopy(session) if isinstance(session, dict) else None

    def active(self, *, extension: bool = False) -> dict[str, Any] | None:
        self._expire_old_sessions()
        with self._lock:
            sessions = [
                session
                for session in self._data["sessions"].values()
                if session.get("status") in ACTIVE_STATUSES
                and (not extension or session.get("status") == "selecting")
            ]
            if not sessions:
                return None
            sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            return deepcopy(sessions[0])

    def cancel(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._data["sessions"].get(str(session_id))
            if not isinstance(session, dict):
                return None
            if session.get("status") == "preparing":
                raise RuntimeError("Dyna đang chuẩn bị video nên chưa thể hủy phiên này.")
            session["status"] = "cancelled"
            session["updated_at"] = _iso(self._now())
            self._save_locked()
            return deepcopy(session)

    @staticmethod
    def _clean_urls(value: object) -> list[str]:
        candidates = value if isinstance(value, list) else [value]
        return list(
            dict.fromkeys(
                str(candidate).strip()[:8192]
                for candidate in candidates
                if str(candidate or "").startswith(("http://", "https://"))
            )
        )[:12]

    def complete(
        self,
        session_id: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not items:
            raise ValueError("Hãy chọn ít nhất một video Douyin.")
        if len(items) > MAX_SELECTION_VIDEOS:
            raise ValueError(
                f"Mỗi phiên chỉ được chọn tối đa {MAX_SELECTION_VIDEOS} video."
            )

        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw in items:
            video_id = str(raw.get("video_id") or raw.get("aweme_id") or "").strip()
            if not VIDEO_ID_PATTERN.fullmatch(video_id):
                raise ValueError(f"ID video Douyin không hợp lệ: {video_id or '(trống)'}")
            if video_id in seen_ids:
                continue
            seen_ids.add(video_id)
            content_type = str(raw.get("content_type") or "video").casefold()
            if content_type != "video":
                continue
            download_urls = self._clean_urls(
                raw.get("download_urls") or raw.get("download_url")
            )
            normalized.append(
                {
                    "video_id": video_id,
                    "aweme_id": video_id,
                    "source_url": self._validated_source_url(
                        raw.get("source_url"), video_id
                    ),
                    "description": str(raw.get("description") or "")[:10000],
                    "author_uid": str(raw.get("author_uid") or "")[:256],
                    "author_nickname": str(raw.get("author_nickname") or "")[:512],
                    "create_time": max(0, int(raw.get("create_time") or 0)),
                    "duration_ms": max(0, int(raw.get("duration_ms") or 0)),
                    "like_count": max(0, int(raw.get("like_count") or 0)),
                    "play_count": max(0, int(raw.get("play_count") or 0)),
                    "thumbnail_url": str(raw.get("thumbnail_url") or "")[:8192],
                    "download_url": download_urls[0] if download_urls else "",
                    "download_urls": download_urls,
                    "referer": str(raw.get("referer") or raw.get("source_url") or "")[
                        :4096
                    ],
                    "user_agent": str(raw.get("user_agent") or "")[:1024],
                    "selected_order": len(normalized) + 1,
                    "content_type": "video",
                    "status": "selected",
                    "last_error": "",
                }
            )
        if not normalized:
            raise ValueError("Danh sách không có video thường nào được hỗ trợ.")

        with self._lock:
            session = self._data["sessions"].get(str(session_id))
            if not isinstance(session, dict):
                raise FileNotFoundError("Không tìm thấy phiên chọn Douyin.")
            if session.get("status") != "selecting":
                raise RuntimeError("Phiên chọn Douyin này không còn nhận danh sách mới.")
            session["items"] = normalized
            session["selected_count"] = len(normalized)
            session["status"] = "ready"
            session["focus_requested"] = True
            session["updated_at"] = _iso(self._now())
            session["last_error"] = ""
            self._save_locked()
            return deepcopy(session)

    def submit(
        self,
        session_id: str,
        *,
        batch_name: str = "",
        items: list[dict[str, Any]],
        targets: tuple[ManualPublishTarget, ...],
    ) -> dict[str, Any]:
        if not targets:
            raise ValueError("Hãy chọn ít nhất một Profile và nền tảng đăng.")
        if not items:
            raise ValueError("Bản nháp Douyin không còn video nào.")
        if len(items) > MAX_SELECTION_VIDEOS:
            raise ValueError(
                f"Mỗi lần chỉ được chuẩn bị tối đa {MAX_SELECTION_VIDEOS} video."
            )

        with self._lock:
            session = self._data["sessions"].get(str(session_id))
            if not isinstance(session, dict):
                raise FileNotFoundError("Không tìm thấy bản nháp Douyin.")
            if session.get("status") != "ready":
                raise RuntimeError("Bản nháp Douyin chưa sẵn sàng để tạo lịch.")
            available = {
                str(item.get("video_id")): deepcopy(item)
                for item in session.get("items") or []
            }
            requested: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for raw in items:
                video_id = str(raw.get("video_id") or "").strip()
                item = available.get(video_id)
                if not item or video_id in seen_ids:
                    continue
                seen_ids.add(video_id)
                caption = str(raw.get("caption") or "").strip()
                if not caption:
                    raise ValueError(
                        f"Video {len(requested) + 1} chưa có mô tả đăng."
                    )
                item["caption"] = caption[:10000]
                item["scheduled_at"] = str(raw.get("scheduled_at") or "")[:64]
                item["selected_order"] = len(requested) + 1
                item["status"] = "waiting_download"
                item["last_error"] = ""
                requested.append(item)
            if not requested:
                raise ValueError("Không tìm thấy video hợp lệ trong bản nháp.")

            session["items"] = requested
            session["selected_count"] = len(requested)
            session["status"] = "preparing"
            session["focus_requested"] = False
            session["updated_at"] = _iso(self._now())
            session["last_error"] = ""
            session["batch_name"] = str(batch_name or "").strip()[:160]
            session["publish_targets"] = [
                {
                    "profile_id": target.profile_id,
                    "platforms": list(target.platforms),
                }
                for target in targets
            ]
            self._save_locked()

            worker = threading.Thread(
                target=self._prepare_and_publish,
                args=(str(session_id), tuple(targets)),
                daemon=True,
                name=f"douyin-selection-{str(session_id)[:8]}",
            )
            self._threads[str(session_id)] = worker
            worker.start()
            return deepcopy(session)

    def _set_item_result(
        self,
        session_id: str,
        video_id: str,
        *,
        status: str,
        file_path: str = "",
        error: str = "",
    ) -> None:
        with self._lock:
            session = self._data["sessions"].get(session_id)
            if not isinstance(session, dict):
                return
            for item in session.get("items") or []:
                if str(item.get("video_id")) != video_id:
                    continue
                item["status"] = status
                item["file_path"] = file_path
                item["last_error"] = error
                break
            session["updated_at"] = _iso(self._now())
            self._save_locked()

    def _prepare_and_publish(
        self,
        session_id: str,
        targets: tuple[ManualPublishTarget, ...],
    ) -> None:
        try:
            snapshot = self.get(session_id)
            if not snapshot:
                return
            items = list(snapshot.get("items") or [])
            destination = self.media_root / session_id
            destination.mkdir(parents=True, exist_ok=True)

            results: dict[str, Path] = {}
            with ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix=f"douyin-{session_id[:6]}",
            ) as executor:
                futures = {}
                for item in items:
                    video_id = str(item["video_id"])
                    self._set_item_result(
                        session_id, video_id, status="downloading"
                    )
                    target = destination / f"{int(item['selected_order']):02d}_{video_id}.mp4"
                    futures[executor.submit(self._downloader, item, target)] = item
                for future in as_completed(futures):
                    item = futures[future]
                    video_id = str(item["video_id"])
                    try:
                        file_path = Path(future.result()).resolve(strict=True)
                    except Exception as exc:
                        message = str(exc) or exc.__class__.__name__
                        logger.warning(
                            "[Chọn Douyin] Không thể tải video %s: %s",
                            video_id,
                            message,
                        )
                        self._set_item_result(
                            session_id,
                            video_id,
                            status="failed_download",
                            error=message,
                        )
                    else:
                        results[video_id] = file_path
                        self._set_item_result(
                            session_id,
                            video_id,
                            status="downloaded",
                            file_path=str(file_path),
                        )

            # Compact the schedule: every successful video takes the next
            # available slot, so a failed download never leaves a hole.
            schedule_slots = [
                str(item.get("scheduled_at") or "")
                for item in items
            ]
            publish_items: list[ManualPublishItem] = []
            for item in items:
                video_id = str(item["video_id"])
                file_path = results.get(video_id)
                if not file_path:
                    continue
                scheduled_at = (
                    schedule_slots[len(publish_items)]
                    if len(publish_items) < len(schedule_slots)
                    else ""
                )
                publish_items.append(
                    ManualPublishItem(
                        file_path=str(file_path),
                        caption=str(item.get("caption") or ""),
                        scheduled_at=scheduled_at,
                        video_id=video_id,
                        source_url=str(item.get("source_url") or ""),
                        source_label=(
                            "Douyin đã chọn"
                            + (
                                f" · {item.get('author_nickname')}"
                                if item.get("author_nickname")
                                else ""
                            )
                        ),
                        create_time=int(item.get("create_time") or 0),
                        duration_ms=int(item.get("duration_ms") or 0),
                        like_count=int(item.get("like_count") or 0),
                        play_count=int(item.get("play_count") or 0),
                        author_uid=str(item.get("author_uid") or ""),
                        author_nickname=str(item.get("author_nickname") or ""),
                        download_url=str(item.get("download_url") or ""),
                    )
                )
            if not publish_items:
                raise RuntimeError("Không tải được video nào trong danh sách đã chọn.")

            result = self.manual_publish.submit(
                ManualPublishRequest(
                    batch_name=str(snapshot.get("batch_name") or ""),
                    items=tuple(publish_items),
                    targets=targets,
                )
            )
            with self._lock:
                session = self._data["sessions"].get(session_id)
                if isinstance(session, dict):
                    session["status"] = "published"
                    session["publish_result"] = result
                    session["published_count"] = len(publish_items)
                    session["failed_count"] = len(items) - len(publish_items)
                    session["updated_at"] = _iso(self._now())
                    session["last_error"] = ""
                    self._save_locked()
        except Exception as exc:
            logger.exception(
                "[Chọn Douyin] Không thể chuẩn bị phiên %s: %s", session_id, exc
            )
            with self._lock:
                session = self._data["sessions"].get(session_id)
                if isinstance(session, dict):
                    session["status"] = "ready"
                    session["last_error"] = str(exc)
                    session["updated_at"] = _iso(self._now())
                    self._save_locked()
        finally:
            with self._lock:
                self._threads.pop(session_id, None)

    @staticmethod
    def _download_direct(item: dict[str, Any], destination: Path) -> Path:
        candidates = list(
            dict.fromkeys(
                [
                    str(item.get("download_url") or "").strip(),
                    *[
                        str(value or "").strip()
                        for value in item.get("download_urls") or []
                    ],
                ]
            )
        )
        candidates = [
            value for value in candidates if value.startswith(("http://", "https://"))
        ]
        if not candidates:
            raise RuntimeError(
                "Extension chưa lấy được link tải trực tiếp; hãy tải lại trang Douyin và chọn lại video."
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Referer": str(item.get("referer") or item.get("source_url") or "https://www.douyin.com/"),
            "User-Agent": str(
                item.get("user_agent")
                or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"
            ),
        }
        errors: list[str] = []
        for index, url in enumerate(candidates, start=1):
            try:
                if partial.exists():
                    partial.unlink()
                bytes_written = 0
                with requests.get(
                    url,
                    headers=headers,
                    stream=True,
                    allow_redirects=True,
                    timeout=(20, 180),
                ) as response:
                    response.raise_for_status()
                    content_type = str(
                        response.headers.get("content-type") or ""
                    ).casefold()
                    if "text/html" in content_type or "application/json" in content_type:
                        raise RuntimeError(
                            f"Máy chủ trả về {content_type or 'nội dung không phải video'}."
                        )
                    with partial.open("wb") as handle:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            handle.write(chunk)
                            bytes_written += len(chunk)
                        handle.flush()
                        os.fsync(handle.fileno())
                if bytes_written < 1024:
                    raise RuntimeError(f"File tải về quá nhỏ ({bytes_written} byte).")
                validate_video_file(str(partial), require_audio=True)
                os.replace(partial, destination)
                return destination
            except Exception as exc:
                errors.append(f"URL {index}: {exc}")
                try:
                    if partial.exists():
                        partial.unlink()
                except OSError:
                    pass
        raise RuntimeError(" | ".join(errors))
