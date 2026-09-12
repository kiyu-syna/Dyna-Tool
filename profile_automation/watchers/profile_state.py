from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from core.runtime_paths import profile_state_dir


class ProfileState:
    """Thread-safe persisted set of processed video IDs for one source."""

    def __init__(
        self,
        profile_id: str,
        source_key: str,
        state_dir: str | None = None,
    ):
        self.profile_id = profile_id
        resolved_dir = state_dir or str(profile_state_dir())
        self.path = (
            Path(resolved_dir)
            / f"profile_{profile_id}_source_{source_key}_seen.json"
        )
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as file_handle:
                    return json.load(file_handle)
            except Exception:
                pass
        return {"seen_ids": [], "last_check": None, "last_video_create_time": 0}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as file_handle:
            json.dump(self._data, file_handle, ensure_ascii=False, indent=2)

    @property
    def seen_ids(self) -> set[str]:
        with self._lock:
            return set(self._data.get("seen_ids", []))

    @property
    def last_video_create_time(self) -> int:
        with self._lock:
            return self._data.get("last_video_create_time", 0)

    def mark_seen(self, video_id: str, create_time: int = 0) -> None:
        with self._lock:
            seen_ids = self._data.setdefault("seen_ids", [])
            if video_id not in seen_ids:
                seen_ids.append(video_id)
                self._data["seen_ids"] = seen_ids[-500:]
            if create_time > self._data.get("last_video_create_time", 0):
                self._data["last_video_create_time"] = create_time
            self._data["last_check"] = datetime.now().isoformat()
            self._save()

    def replace_seen(
        self,
        video_ids: list[str],
        last_video_create_time: int = 0,
    ) -> None:
        with self._lock:
            deduped_ids = list(dict.fromkeys(video_id for video_id in video_ids if video_id))
            self._data["seen_ids"] = deduped_ids[-500:]
            self._data["last_video_create_time"] = int(last_video_create_time or 0)
            self._data["last_check"] = datetime.now().isoformat()
            self._save()

    def is_new_video(self, video_id: str, _create_time: int) -> bool:
        with self._lock:
            return video_id not in set(self._data.get("seen_ids", []))

    def touch(self) -> None:
        """Record a successful comparison even when it found no new video."""
        with self._lock:
            self._data["last_check"] = datetime.now().isoformat()
            self._save()
