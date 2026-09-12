from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import core.config as config
from core.runtime_paths import profile_state_dir
from application.tracking.sources import (
    get_tracking_sources,
    serialize_tracking_sources,
    tracking_source_label,
)
from services.browser.windows_secret_service import (
    SecretProtectionError,
    protect_secret,
    unprotect_secret,
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
        self.state_dir = Path(state_dir or profile_state_dir(root))

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

    @staticmethod
    def _normalize_proxy_config(value: Any, existing_value: Any = None) -> dict[str, Any]:
        proxy = dict(value or {})
        existing = dict(existing_value or {})
        enabled = bool(proxy.get("enabled", False))
        server = str(proxy.get("server") or "").strip()
        if server and "://" not in server:
            server = f"http://{server}"
        if server:
            try:
                parsed = urlsplit(server)
                _ = parsed.port
            except ValueError as exc:
                raise ValueError("Địa chỉ hoặc cổng proxy không hợp lệ.") from exc
            if parsed.scheme.casefold() not in {"http", "https", "socks5"}:
                raise ValueError("Proxy chỉ hỗ trợ giao thức HTTP, HTTPS hoặc SOCKS5.")
            if not parsed.hostname:
                raise ValueError("Địa chỉ proxy phải có hostname và cổng hợp lệ.")
            if parsed.username or parsed.password:
                raise ValueError("Hãy nhập tài khoản proxy ở ô riêng, không đặt trong URL.")
        if enabled and not server:
            raise ValueError("Hãy nhập địa chỉ proxy trước khi bật proxy.")

        username = str(proxy.get("username") or "").strip()
        bypass = ",".join(
            part.strip() for part in str(proxy.get("bypass") or "").split(",") if part.strip()
        )
        encrypted_password = str(proxy.get("password_encrypted") or "").strip()
        password = str(proxy.get("password") or "")
        password_set = bool(
            proxy.get("password_set", bool(password or encrypted_password))
        )
        if password:
            try:
                encrypted_password = protect_secret(password)
            except SecretProtectionError as exc:
                raise ValueError(str(exc)) from exc
        elif password_set:
            encrypted_password = encrypted_password or str(
                existing.get("password_encrypted") or ""
            ).strip()
        else:
            encrypted_password = ""

        return {
            "enabled": enabled,
            "server": server,
            "username": username,
            "password_encrypted": encrypted_password,
            "bypass": bypass,
        }

    @classmethod
    def _normalize_browser_config(
        cls,
        value: Any,
        existing_value: Any = None,
    ) -> dict[str, Any]:
        browser = dict(value or {})
        existing_browser = dict(existing_value or {})
        provider = str(browser.get("provider") or "local_chromium").strip().casefold()
        if provider not in {"gemlogin", "local_chromium"}:
            raise ValueError("Loại trình duyệt phải là GemLogin hoặc Local Chromium.")

        user_data_dir = str(browser.get("user_data_dir") or "").strip()
        executable_path = str(browser.get("executable_path") or "").strip()
        profile_directory = str(browser.get("profile_directory") or "Default").strip()
        if not profile_directory or any(char in profile_directory for char in ("/", "\\", ":")):
            raise ValueError("Tên Chromium profile directory không hợp lệ.")

        try:
            launch_timeout_ms = max(5_000, int(browser.get("launch_timeout_ms") or 60_000))
        except (TypeError, ValueError) as exc:
            raise ValueError("Thời gian chờ mở Chromium phải là số nguyên.") from exc
        login_mode = str(browser.get("login_mode") or "native").strip().casefold()
        if login_mode not in {"native", "playwright"}:
            login_mode = "native"
        fingerprint_mode = str(
            browser.get("fingerprint_mode") or "system"
        ).strip().casefold()
        if fingerprint_mode != "system":
            fingerprint_mode = "system"

        if provider == "local_chromium":
            if bool(user_data_dir) != bool(executable_path):
                raise ValueError(
                    "Local Chromium cần đủ User Data Directory và đường dẫn executable."
                )
            if user_data_dir and (not Path(user_data_dir).is_absolute() or not Path(executable_path).is_absolute()):
                raise ValueError("Đường dẫn Local Chromium phải là đường dẫn tuyệt đối.")
            normalized = user_data_dir.replace("/", "\\").casefold()
            if user_data_dir and "\\.gemlogin\\profile\\profiles\\" in normalized:
                raise ValueError(
                    "Không được dùng profile gốc GemLogin; hãy chọn một bản copy riêng."
                )

        return {
            "provider": provider,
            "user_data_dir": user_data_dir,
            "executable_path": executable_path,
            "profile_directory": profile_directory,
            "headless": bool(browser.get("headless", False)),
            "background": bool(browser.get("background", True)),
            "launch_timeout_ms": launch_timeout_ms,
            "login_mode": login_mode,
            "fingerprint_mode": fingerprint_mode,
            "proxy": cls._normalize_proxy_config(
                browser.get("proxy"),
                existing_browser.get("proxy"),
            ),
        }

    @staticmethod
    def _with_decrypted_browser_secret(profile: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(profile)
        browser = dict(result.get("browser") or {})
        proxy = dict(browser.get("proxy") or {})
        encrypted = str(proxy.get("password_encrypted") or "")
        try:
            proxy["password"] = unprotect_secret(encrypted) if encrypted else ""
        except SecretProtectionError as exc:
            raise ValueError(str(exc)) from exc
        proxy["password_set"] = bool(encrypted)
        browser["proxy"] = proxy
        result["browser"] = browser
        return result

    @staticmethod
    def redact_secrets(profile: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(profile)
        browser = dict(result.get("browser") or {})
        proxy = dict(browser.get("proxy") or {})
        password_set = bool(proxy.get("password_set") or proxy.get("password_encrypted"))
        proxy.pop("password_encrypted", None)
        proxy["password"] = ""
        proxy["password_set"] = password_set
        browser["proxy"] = proxy
        result["browser"] = browser
        return result

    def load(self, profile_id: str) -> dict[str, Any]:
        path = self._profile_path(profile_id)
        if not path.exists():
            raise FileNotFoundError(f"Không tìm thấy Profile {profile_id}.")
        with path.open("r", encoding="utf-8") as handle:
            profile = json.load(handle)
        if not isinstance(profile, dict):
            raise ValueError(f"Cấu hình Profile {profile_id} không hợp lệ.")
        profile["initial_scan_mode"] = str(
            profile.get("initial_scan_mode") or "skip_existing"
        ).strip().casefold()
        if profile["initial_scan_mode"] not in {"skip_existing", "process_latest"}:
            profile["initial_scan_mode"] = "skip_existing"
        profile["browser"] = self._normalize_browser_config(profile.get("browser"))
        return self._with_decrypted_browser_secret(profile)

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
            "caption_options": {
                "tiktok_use_original_desc": False,
                "douyin_use_original_desc": False,
                "telegram_use_custom_caption": False,
                "telegram_pin_caption_message": False,
            },
            "check_interval_minutes": 30,
            "processing_priority": 100,
            "initial_scan_mode": "skip_existing",
            "browser": self._normalize_browser_config({}),
            "tracking_sources": [],
            "douyin": {
                "gemlogin_profile_id": profile_id,
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
                for source in get_tracking_sources(profile, include_disabled=True):
                    identity = source.get("sec_uid") or source.get("unique_id")
                    owners[f"{source['platform']}:{identity}"] = other_id
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
        initial_scan_mode = str(
            profile.get("initial_scan_mode") or "skip_existing"
        ).strip().casefold()
        if initial_scan_mode not in {"skip_existing", "process_latest"}:
            raise ValueError("Chế độ quét lần đầu không hợp lệ.")
        profile["initial_scan_mode"] = initial_scan_mode
        existing_browser: dict[str, Any] = {}
        existing_path = self._profile_path(profile_id)
        if existing_path.is_file():
            try:
                existing_profile = json.loads(existing_path.read_text(encoding="utf-8"))
                existing_browser = dict(existing_profile.get("browser") or {})
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
                existing_browser = {}
        profile["browser"] = self._normalize_browser_config(
            profile.get("browser"),
            existing_browser,
        )

        douyin = dict(profile.get("douyin") or {})
        raw_tracking_sources = profile.get("tracking_sources")
        if not isinstance(raw_tracking_sources, list):
            raise ValueError("Danh sách nguồn theo dõi không hợp lệ.")

        serialized_tracking = serialize_tracking_sources(
            raw_tracking_sources,
            profile["check_interval_minutes"],
        )
        normalized_sources = get_tracking_sources(
            {**profile, "tracking_sources": serialized_tracking},
            include_disabled=True,
        )
        seen_identities: set[str] = set()
        owners = self._other_source_owners(profile_id)
        for index, source in enumerate(serialized_tracking, start=1):
            identity_value = source.get("sec_uid") or source.get("unique_id")
            identity = f"{source['platform']}:{identity_value}"
            if identity in seen_identities:
                raise ValueError(f"Nguồn theo dõi dòng {index} bị trùng trong Profile này.")
            seen_identities.add(identity)

        for source in normalized_sources:
            identity_value = source.get("sec_uid") or source.get("unique_id")
            identity = f"{source['platform']}:{identity_value}"
            if identity in owners:
                raise ValueError(
                    f"Nguồn {identity_value} đã thuộc Profile {owners[identity]}; "
                    "mỗi nguồn chỉ được thuộc một Profile."
                )

        profile["tracking_sources"] = serialized_tracking
        douyin["gemlogin_profile_id"] = str(
            douyin.get("gemlogin_profile_id") or profile_id
        ).strip()
        profile["douyin"] = {
            "gemlogin_profile_id": douyin["gemlogin_profile_id"],
        }

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

        caption_options = dict(profile.get("caption_options") or {})
        profile["caption_options"] = {
            "tiktok_use_original_desc": bool(caption_options.get("tiktok_use_original_desc", False)),
            "douyin_use_original_desc": bool(caption_options.get("douyin_use_original_desc", False)),
            "telegram_use_custom_caption": bool(
                caption_options.get("telegram_use_custom_caption", False)
            ),
            "telegram_pin_caption_message": bool(
                caption_options.get("telegram_pin_caption_message", False)
            ),
        }

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
        return self._with_decrypted_browser_secret(profile)

    def delete(self, profile_id: str) -> None:
        path = self._profile_path(profile_id)
        if not path.exists():
            raise FileNotFoundError(f"Không tìm thấy Profile {profile_id}.")
        path.unlink()

    def _seen_path(self, profile_id: str, source_key: str) -> Path:
        profile_id = self.normalize_profile_id(profile_id)
        profile = self.load(profile_id)
        allowed = {
            source["source_key"]
            for source in get_tracking_sources(profile, include_disabled=True)
        }
        if source_key not in allowed:
            raise FileNotFoundError("Không tìm thấy nguồn theo dõi trong Profile.")
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
            get_tracking_sources(profile, include_disabled=True), start=1
        ):
            path = self._seen_path(profile_id, source["source_key"])
            payload = self._read_seen_payload(path)
            label = tracking_source_label(source)
            rows.append(
                {
                    "source_key": source["source_key"],
                    "label": f"{index}. {label}",
                    "platform": source["platform"],
                    "exists": path.exists(),
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
