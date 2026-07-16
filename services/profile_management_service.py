from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import core.config as config
from profile_automation.douyin_sources import (
    get_douyin_sources,
    normalize_sec_uid,
    serialize_douyin_sources,
)


class ProfileManagementService:
    """Owns profile configuration and per-source baseline state files."""

    def __init__(
        self,
        profile_dir: str | os.PathLike | None = None,
        state_dir: str | os.PathLike | None = None,
    ):
        root = Path(config.BASE_DIR)
        self.profile_dir = Path(profile_dir or root / "profile_automation" / "profiles")
        self.state_dir = Path(state_dir or root / "profile_automation" / "state")

    @staticmethod
    def normalize_profile_id(profile_id: str) -> str:
        normalized = str(profile_id or "").strip()
        if not normalized.isdigit():
            raise ValueError("Profile ID phải là số.")
        return normalized

    def _profile_path(self, profile_id: str) -> Path:
        return self.profile_dir / f"profile_{self.normalize_profile_id(profile_id)}.json"

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def load(self, profile_id: str) -> dict[str, Any]:
        path = self._profile_path(profile_id)
        if not path.exists():
            raise FileNotFoundError(f"Không tìm thấy Profile {profile_id}.")
        with path.open("r", encoding="utf-8") as handle:
            profile = json.load(handle)
        if not isinstance(profile, dict):
            raise ValueError(f"Cấu hình Profile {profile_id} không hợp lệ.")
        return profile

    def create(self, profile_id: str, name: str = "") -> dict[str, Any]:
        profile_id = self.normalize_profile_id(profile_id)
        path = self._profile_path(profile_id)
        if path.exists():
            raise FileExistsError(f"Profile {profile_id} đã tồn tại.")
        profile = {
            "id": profile_id,
            "name": str(name or f"Profile {profile_id}").strip(),
            "enabled": True,
            "default_caption": "",
            "check_interval_minutes": 30,
            "processing_priority": 100,
            "douyin": {
                "gemlogin_profile_id": profile_id,
                "sources": [],
                "target_sec_uid": "",
                "target_display_name": "",
                "legacy_state_sec_uid": "",
            },
            "tiktok": {
                "gemlogin_profile_id": profile_id,
                "enabled": False,
                "use_original_desc": False,
            },
            "facebook": {
                "gemlogin_profile_id": profile_id,
                "enabled": False,
                "use_original_desc": False,
                "profile_url": "https://www.facebook.com/me",
            },
            "youtube": {
                "gemlogin_profile_id": profile_id,
                "enabled": False,
                "use_original_desc": False,
                "channel_id": "",
                "preset": "slow",
                "crf": 16,
            },
            "filters": {"min_likes": 5000, "max_duration_seconds": 120},
            "save_dir": "",
        }
        self._atomic_write(path, profile)
        return profile

    def _other_source_owners(self, profile_id: str) -> dict[str, str]:
        owners: dict[str, str] = {}
        if not self.profile_dir.exists():
            return owners
        for path in self.profile_dir.glob("profile_*.json"):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    profile = json.load(handle)
                other_id = str(profile.get("id") or path.stem.removeprefix("profile_"))
                if other_id == profile_id:
                    continue
                for source in get_douyin_sources(profile, include_disabled=True):
                    owners[source["target_sec_uid"]] = other_id
            except Exception:
                continue
        return owners

    def save(self, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        profile_id = self.normalize_profile_id(profile_id)
        profile = dict(payload or {})
        if str(profile.get("id") or profile_id) != profile_id:
            raise ValueError("Không thể thay đổi Profile ID.")
        profile["id"] = profile_id
        profile["name"] = str(profile.get("name") or f"Profile {profile_id}").strip()
        profile["enabled"] = bool(profile.get("enabled", True))

        try:
            interval = int(profile.get("check_interval_minutes") or 30)
        except (TypeError, ValueError) as exc:
            raise ValueError("Chu kỳ kiểm tra phải là số nguyên.") from exc
        if interval <= 0:
            raise ValueError("Chu kỳ kiểm tra phải lớn hơn 0 phút.")
        profile["check_interval_minutes"] = interval

        try:
            processing_priority = int(profile.get("processing_priority", 100))
        except (TypeError, ValueError) as exc:
            raise ValueError("Độ ưu tiên xử lý phải là số nguyên.") from exc
        if not 0 <= processing_priority <= 1000:
            raise ValueError("Độ ưu tiên xử lý phải nằm trong khoảng 0 đến 1000.")
        profile["processing_priority"] = processing_priority

        douyin = dict(profile.get("douyin") or {})
        raw_sources = douyin.get("sources") or []
        if not isinstance(raw_sources, list):
            raise ValueError("Danh sách nguồn Douyin không hợp lệ.")
        sources = []
        seen_uids: set[str] = set()
        owners = self._other_source_owners(profile_id)
        for index, source in enumerate(raw_sources, start=1):
            if not isinstance(source, dict):
                continue
            sec_uid = normalize_sec_uid(source.get("target_sec_uid"))
            if not sec_uid:
                continue
            if sec_uid in seen_uids:
                raise ValueError(f"Nguồn Douyin dòng {index} bị trùng trong Profile này.")
            if sec_uid in owners:
                raise ValueError(
                    f"Nguồn {sec_uid} đã thuộc Profile {owners[sec_uid]}; "
                    "mỗi nguồn chỉ được thuộc một Profile."
                )
            seen_uids.add(sec_uid)
            sources.append(source)

        serialized_sources = serialize_douyin_sources(sources)
        first_source = serialized_sources[0] if serialized_sources else {}
        douyin["gemlogin_profile_id"] = str(
            douyin.get("gemlogin_profile_id") or profile_id
        ).strip()
        douyin["sources"] = serialized_sources
        douyin["target_sec_uid"] = first_source.get("target_sec_uid", "")
        douyin["target_display_name"] = first_source.get("target_display_name", "")
        douyin["legacy_state_sec_uid"] = normalize_sec_uid(
            douyin.get("legacy_state_sec_uid") or douyin.get("target_sec_uid")
        )
        profile["douyin"] = douyin

        filters = dict(profile.get("filters") or {})
        try:
            filters["min_likes"] = max(0, int(filters.get("min_likes") or 0))
            filters["max_duration_seconds"] = float(
                filters.get("max_duration_seconds") or 120
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Bộ lọc video chứa giá trị không hợp lệ.") from exc
        if filters["max_duration_seconds"] <= 0:
            raise ValueError("Độ dài video tối đa phải lớn hơn 0 giây.")
        profile["filters"] = filters

        for platform in ("tiktok", "facebook", "youtube"):
            section = dict(profile.get(platform) or {})
            section["enabled"] = bool(section.get("enabled", False))
            section["use_original_desc"] = bool(section.get("use_original_desc", False))
            section["gemlogin_profile_id"] = str(
                section.get("gemlogin_profile_id") or profile_id
            ).strip()
            profile[platform] = section

        youtube = profile["youtube"]
        youtube["preset"] = str(youtube.get("preset") or "slow")
        try:
            youtube["crf"] = int(youtube.get("crf") or 16)
        except (TypeError, ValueError) as exc:
            raise ValueError("CRF YouTube phải là số nguyên.") from exc
        if not 0 <= youtube["crf"] <= 51:
            raise ValueError("CRF YouTube phải nằm trong khoảng 0 đến 51.")

        self._atomic_write(self._profile_path(profile_id), profile)
        return profile

    def delete(self, profile_id: str) -> None:
        path = self._profile_path(profile_id)
        if not path.exists():
            raise FileNotFoundError(f"Không tìm thấy Profile {profile_id}.")
        path.unlink()

    def _seen_path(self, profile_id: str, source_key: str) -> Path:
        profile_id = self.normalize_profile_id(profile_id)
        if source_key == "legacy":
            return self.state_dir / f"profile_{profile_id}_seen.json"
        profile = self.load(profile_id)
        allowed = {
            source["source_key"]
            for source in get_douyin_sources(profile, include_disabled=True)
        }
        if source_key not in allowed:
            raise FileNotFoundError("Không tìm thấy nguồn Douyin trong Profile.")
        return self.state_dir / f"profile_{profile_id}_source_{source_key}_seen.json"

    @staticmethod
    def _read_seen_payload(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {
                "seen_ids": [],
                "last_check": None,
                "last_video_create_time": 0,
            }
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("Dữ liệu đối chiếu phải là một JSON object.")
        return payload

    def list_seen_sources(self, profile_id: str) -> list[dict[str, Any]]:
        profile_id = self.normalize_profile_id(profile_id)
        profile = self.load(profile_id)
        rows = []
        for index, source in enumerate(
            get_douyin_sources(profile, include_disabled=True), start=1
        ):
            path = self._seen_path(profile_id, source["source_key"])
            payload = self._read_seen_payload(path)
            label = source.get("target_display_name") or f"...{source['target_sec_uid'][-10:]}"
            rows.append(
                {
                    "source_key": source["source_key"],
                    "label": f"{index}. {label}",
                    "exists": path.exists(),
                    "seen_count": len(payload.get("seen_ids") or []),
                    "last_check": payload.get("last_check"),
                }
            )
        legacy = self.state_dir / f"profile_{profile_id}_seen.json"
        if not rows and legacy.exists():
            payload = self._read_seen_payload(legacy)
            rows.append(
                {
                    "source_key": "legacy",
                    "label": "Dữ liệu cũ",
                    "exists": True,
                    "seen_count": len(payload.get("seen_ids") or []),
                    "last_check": payload.get("last_check"),
                }
            )
        return rows

    def read_seen(self, profile_id: str, source_key: str) -> dict[str, Any]:
        path = self._seen_path(profile_id, source_key)
        return {"source_key": source_key, "exists": path.exists(), "data": self._read_seen_payload(path)}

    def save_seen(
        self,
        profile_id: str,
        source_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Dữ liệu đối chiếu phải là một JSON object.")
        raw_ids = payload.get("seen_ids") or []
        if not isinstance(raw_ids, list):
            raise ValueError("Trường seen_ids phải là một mảng.")
        seen_ids = []
        seen = set()
        for value in raw_ids:
            video_id = str(value or "").strip()
            if video_id and video_id not in seen:
                seen.add(video_id)
                seen_ids.append(video_id)
        try:
            create_time = int(payload.get("last_video_create_time") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("last_video_create_time phải là số nguyên.") from exc
        normalized = {
            "seen_ids": seen_ids[-500:],
            "last_check": payload.get("last_check"),
            "last_video_create_time": max(0, create_time),
        }
        self._atomic_write(self._seen_path(profile_id, source_key), normalized)
        return normalized
