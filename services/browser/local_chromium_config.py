from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.browser.windows_secret_service import (
    SecretProtectionError,
    unprotect_secret,
)


@dataclass(frozen=True)
class LocalProxyConfig:
    server: str
    username: str = ""
    password: str = ""
    bypass: str = ""

    def as_playwright_options(self) -> dict[str, str]:
        options = {"server": self.server}
        if self.username:
            options["username"] = self.username
        if self.password:
            options["password"] = self.password
        if self.bypass:
            options["bypass"] = self.bypass
        return options


@dataclass(frozen=True)
class LocalChromiumConfig:
    user_data_dir: Path
    executable_path: Path
    profile_directory: str = "Default"
    headless: bool = False
    background: bool = True
    launch_timeout_ms: int = 60_000
    proxy: LocalProxyConfig | None = None

    @property
    def key(self) -> str:
        return normalized_path(self.user_data_dir)


class LocalChromiumError(RuntimeError):
    pass


def classify_local_chromium_error(error: object) -> str:
    message = str(error or "").strip()
    normalized = message.casefold()
    if "2147483651" in message or "0x80000003" in normalized:
        return "profile_crash"
    if any(
        marker in normalized
        for marker in (
            "processsingleton",
            "profile in use",
            "profile đang được sử dụng",
            "user data directory is already in use",
            "một tiến trình dyna khác sử dụng",
        )
    ):
        return "profile_in_use"
    if any(
        marker in normalized
        for marker in (
            "executable doesn't exist",
            "executable not found",
            "executable không tồn tại",
            "không tìm thấy chromium runtime",
        )
    ):
        return "runtime_missing"
    if "yêu cầu iron/chromium" in normalized or "dùng sai runtime" in normalized:
        return "runtime_mismatch"
    if "target page, context or browser has been closed" in normalized:
        return "browser_closed"
    if any(marker in normalized for marker in ("timeout", "timed out", "thời gian chờ")):
        return "launch_timeout"
    if "playwright" in normalized and any(
        marker in normalized for marker in ("driver", "connection closed", "pipe")
    ):
        return "playwright_driver"
    return "unknown"


def local_chromium_recovery_action(error: object) -> str:
    return {
        "profile_crash": "Dyna sẽ thử chế độ an toàn; nếu vẫn lỗi, hãy tạo lại bản sao Profile.",
        "profile_in_use": "Đóng Chromium đang dùng Profile này rồi bấm Kiểm tra & sửa.",
        "runtime_missing": "Cài hoặc chọn lại Chromium runtime.",
        "runtime_mismatch": "Chọn đúng phiên bản Iron/Chromium của bản sao GemLogin.",
        "browser_closed": "Kiểm tra bản sao Profile rồi thử chế độ an toàn.",
        "launch_timeout": "Đóng tiến trình Chromium còn treo rồi thử lại.",
        "playwright_driver": "Khởi động lại Dyna để tạo lại Playwright driver.",
        "unknown": "Kiểm tra runtime và tạo lại bản sao nếu lỗi lặp lại.",
    }[classify_local_chromium_error(error)]


def summarize_local_chromium_error(error: object) -> str:
    message = str(error or "").strip()
    normalized = message.casefold()
    if "2147483651" in message or "0x80000003" in normalized:
        return (
            "Chromium tự đóng ngay khi mở profile (mã 0x80000003). "
            "Bản sao profile có thể hỏng hoặc không tương thích; hãy tạo lại bản sao."
        )
    if classify_local_chromium_error(error) == "profile_in_use":
        return "Profile Chromium đang được tiến trình khác sử dụng. Hãy đóng nó rồi thử lại."
    if "executable doesn't exist" in normalized or "executable not found" in normalized:
        return "Không tìm thấy Chromium runtime đã cấu hình."
    if "target page, context or browser has been closed" in normalized:
        return "Chromium đã đóng trước khi Dyna kết nối được."
    first_line = next(
        (line.strip() for line in message.splitlines() if line.strip()),
        "Lỗi không xác định",
    )
    return first_line if len(first_line) <= 420 else f"{first_line[:417]}..."


def normalized_path(value: str | os.PathLike) -> str:
    return os.path.normcase(
        os.path.abspath(os.path.normpath(str(value)))
    ).rstrip("\\/")


def _is_inside_original_gemlogin_profiles(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.resolve().parts)
    marker = (".gemlogin", "profile", "profiles")
    return any(
        parts[index : index + len(marker)] == marker
        for index in range(len(parts))
    )


def _contains_original_profile_reference(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_original_profile_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_original_profile_reference(item) for item in value)
    if not isinstance(value, str):
        return False
    return "\\.gemlogin\\profile\\profiles\\" in value.replace("/", "\\").casefold()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LocalChromiumError(f"Không đọc được {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LocalChromiumError(f"Dữ liệu Chromium không hợp lệ: {path}")
    return value


def _gemlogin_copy_expected_major(user_data_dir: Path) -> int | None:
    if not (user_data_dir / "key.txt").is_file():
        return None
    last_browser = user_data_dir / "Last Browser"
    if last_browser.is_file():
        try:
            value = last_browser.read_bytes().decode("utf-16-le").strip("\x00\r\n ")
        except (OSError, UnicodeError):
            value = ""
        for pattern in (
            r"(?i)[\\/]browser[\\/](\d+)[\\/]",
            r"(?i)[\\/]iron-(\d+)[\\/]",
        ):
            match = re.search(pattern, value)
            if match:
                return int(match.group(1))
    last_version = user_data_dir / "Last Version"
    if last_version.is_file():
        try:
            return int(last_version.read_text(encoding="ascii").strip().split(".", 1)[0])
        except (OSError, UnicodeError, ValueError):
            pass
    return None


def _executable_major_version(executable_path: Path) -> int | None:
    if os.name == "nt":
        try:
            import win32api

            info = win32api.GetFileVersionInfo(str(executable_path), "\\")
            return int(win32api.HIWORD(info["FileVersionMS"]))
        except (ImportError, OSError, KeyError, TypeError, ValueError):
            pass
    manifest_path = executable_path.parent / "dyna-runtime.json"
    if manifest_path.is_file():
        try:
            runtime_id = str(_read_json(manifest_path).get("runtime_id") or "")
        except LocalChromiumError:
            runtime_id = ""
        match = re.fullmatch(r"(?i)iron-(\d+)", runtime_id)
        if match:
            return int(match.group(1))
    match = re.search(
        r"(?i)\\browser\\(\d+)\\",
        str(executable_path).replace("/", "\\"),
    )
    return int(match.group(1)) if match else None


def _ensure_gemlogin_runtime_compatible(
    user_data_dir: Path,
    executable_path: Path,
) -> None:
    expected_major = _gemlogin_copy_expected_major(user_data_dir)
    if expected_major is None:
        return
    actual_major = _executable_major_version(executable_path)
    if actual_major is None:
        raise LocalChromiumError(
            f"Không xác định được runtime; Profile yêu cầu Iron/Chromium {expected_major}."
        )
    if actual_major != expected_major:
        raise LocalChromiumError(
            f"Bản copy GemLogin yêu cầu Iron/Chromium {expected_major} nhưng executable "
            f"là phiên bản {actual_major}. Dùng sai runtime có thể làm mất cookie."
        )


def _resolve_local_chromium_config(
    profile_config: dict[str, Any],
    *,
    allow_original_references: bool,
) -> LocalChromiumConfig:
    browser = dict((profile_config or {}).get("browser") or {})
    user_data_value = str(browser.get("user_data_dir") or "").strip()
    executable_value = str(browser.get("executable_path") or "").strip()
    if not user_data_value:
        raise LocalChromiumError("Chưa cấu hình Chromium User Data Directory.")
    if not executable_value:
        raise LocalChromiumError("Chưa cấu hình Chromium executable.")

    user_data_dir = Path(user_data_value)
    executable_path = Path(executable_value)
    if not user_data_dir.is_absolute() or not executable_path.is_absolute():
        raise LocalChromiumError("Đường dẫn browser và profile phải là tuyệt đối.")
    if _is_inside_original_gemlogin_profiles(user_data_dir):
        raise LocalChromiumError(
            "Từ chối mở profile gốc của GemLogin; hãy dùng một bản sao."
        )
    if not user_data_dir.is_dir():
        raise LocalChromiumError(f"User Data Directory không tồn tại: {user_data_dir}")
    if not executable_path.is_file():
        raise LocalChromiumError(f"Chromium executable không tồn tại: {executable_path}")

    profile_directory = str(browser.get("profile_directory") or "Default").strip()
    if not profile_directory or any(
        char in profile_directory for char in ("/", "\\", ":")
    ):
        raise LocalChromiumError("Tên Chromium profile directory không hợp lệ.")
    required = (
        user_data_dir / "Local State",
        user_data_dir / profile_directory,
        user_data_dir / profile_directory / "Preferences",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise LocalChromiumError("User Data Directory thiếu: " + ", ".join(missing))

    _ensure_gemlogin_runtime_compatible(user_data_dir, executable_path)
    local_state = _read_json(user_data_dir / "Local State")
    if (
        not allow_original_references
        and _contains_original_profile_reference(local_state)
    ):
        raise LocalChromiumError(
            "Bản copy còn tham chiếu tuyệt đối về profile gốc GemLogin."
        )

    try:
        launch_timeout_ms = max(
            5_000,
            int(browser.get("launch_timeout_ms") or 60_000),
        )
    except (TypeError, ValueError) as exc:
        raise LocalChromiumError("Thời gian chờ mở Chromium không hợp lệ.") from exc

    proxy = None
    proxy_data = dict(browser.get("proxy") or {})
    if bool(proxy_data.get("enabled", False)):
        server = str(proxy_data.get("server") or "").strip()
        if not server:
            raise LocalChromiumError("Proxy đã bật nhưng chưa có máy chủ.")
        password = str(proxy_data.get("password") or "")
        encrypted_password = str(proxy_data.get("password_encrypted") or "")
        if not password and encrypted_password:
            try:
                password = unprotect_secret(encrypted_password)
            except SecretProtectionError as exc:
                raise LocalChromiumError(str(exc)) from exc
        proxy = LocalProxyConfig(
            server=server,
            username=str(proxy_data.get("username") or "").strip(),
            password=password,
            bypass=str(proxy_data.get("bypass") or "").strip(),
        )

    return LocalChromiumConfig(
        user_data_dir=user_data_dir.resolve(),
        executable_path=executable_path.resolve(),
        profile_directory=profile_directory,
        headless=bool(browser.get("headless", False)),
        background=bool(browser.get("background", True)),
        launch_timeout_ms=launch_timeout_ms,
        proxy=proxy,
    )


def resolve_local_chromium_config(
    profile_config: dict[str, Any],
) -> LocalChromiumConfig:
    return _resolve_local_chromium_config(
        profile_config,
        allow_original_references=False,
    )
