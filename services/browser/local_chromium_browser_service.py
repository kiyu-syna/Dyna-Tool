from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
from playwright.sync_api import sync_playwright
from services.browser.windows_secret_service import SecretProtectionError, unprotect_secret


LOGGER = logging.getLogger(__name__)
ROOT_LOCK_NAMES = (
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
    "DevToolsActivePort",
)
BROWSER_PROCESS_NAMES = {
    "brave.exe",
    "chrome.exe",
    "chromium.exe",
    "iron.exe",
    "msedge.exe",
}
_PROFILE_LOCKS: dict[str, threading.RLock] = {}
_PROFILE_LOCKS_GUARD = threading.RLock()
_PROFILE_HEALTH: dict[str, dict[str, Any]] = {}
_PROFILE_HEALTH_GUARD = threading.RLock()


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


class LocalPersistentBrowser:
    """Small Browser-compatible facade around launch_persistent_context()."""

    is_local_persistent = True

    def __init__(self, context):
        self._context = context
        self._connected = True

    @property
    def contexts(self) -> list:
        return [self._context] if self._connected else []

    def is_connected(self) -> bool:
        return self._connected

    def mark_closed(self) -> None:
        self._connected = False


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
    if any(marker in normalized for marker in ("executable doesn't exist", "executable not found", "executable không tồn tại", "không tìm thấy chromium runtime")):
        return "runtime_missing"
    if "yêu cầu iron/chromium" in normalized or "dùng sai runtime" in normalized:
        return "runtime_mismatch"
    if "target page, context or browser has been closed" in normalized:
        return "browser_closed"
    if "timeout" in normalized or "timed out" in normalized or "thời gian chờ" in normalized:
        return "launch_timeout"
    if "playwright" in normalized and any(marker in normalized for marker in ("driver", "connection closed", "pipe")):
        return "playwright_driver"
    return "unknown"


def local_chromium_recovery_action(error: object) -> str:
    code = classify_local_chromium_error(error)
    return {
        "profile_crash": "Dyna sẽ thử chế độ an toàn; nếu vẫn lỗi, hãy tạo lại bản sao Profile.",
        "profile_in_use": "Đóng cửa sổ Chromium đang dùng Profile này rồi bấm Kiểm tra & sửa.",
        "runtime_missing": "Cài hoặc chọn lại Chromium runtime.",
        "runtime_mismatch": "Chọn đúng phiên bản Iron/Chromium được ghi trong bản sao GemLogin.",
        "browser_closed": "Bấm Kiểm tra & sửa; Dyna sẽ dọn trạng thái crash và thử chế độ an toàn.",
        "launch_timeout": "Đóng tiến trình Chromium còn treo rồi thử lại.",
        "playwright_driver": "Khởi động lại Dyna để tạo lại Playwright driver.",
        "unknown": "Mở chi tiết kỹ thuật, kiểm tra runtime và tạo lại bản sao nếu lỗi lặp lại.",
    }[code]


def summarize_local_chromium_error(error: object) -> str:
    """Turn noisy Playwright launch output into a useful Vietnamese action message."""
    message = str(error or "").strip()
    normalized = message.casefold()
    if "2147483651" in message or "0x80000003" in normalized:
        return (
            "Chromium tự đóng ngay khi mở profile (mã 0x80000003). "
            "Bản sao profile có thể bị hỏng hoặc không tương thích; "
            "hãy tạo lại bản sao từ profile gốc trong tab Tự động trình duyệt."
        )
    if classify_local_chromium_error(error) == "profile_in_use":
        return (
            "Profile Chromium đang được một tiến trình khác sử dụng. "
            "Hãy đóng cửa sổ trình duyệt của profile này rồi thử lại."
        )
    if "executable doesn't exist" in normalized or "executable not found" in normalized:
        return "Không tìm thấy Chromium runtime đã cấu hình. Hãy cài hoặc chọn lại runtime trình duyệt."
    if "target page, context or browser has been closed" in normalized:
        return (
            "Chromium đã đóng trước khi Dyna kết nối được. "
            "Hãy kiểm tra bản sao profile và bảo đảm profile không mở ở ứng dụng khác."
        )
    first_line = next((line.strip() for line in message.splitlines() if line.strip()), "Lỗi không xác định")
    if len(first_line) > 420:
        first_line = f"{first_line[:417]}..."
    return first_line


def normalized_path(value: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(str(value)))).rstrip("\\/")


def _is_inside_original_gemlogin_profiles(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.resolve().parts)
    marker = (".gemlogin", "profile", "profiles")
    return any(parts[index : index + len(marker)] == marker for index in range(len(parts)))


def _contains_original_profile_reference(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_original_profile_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_original_profile_reference(item) for item in value)
    if not isinstance(value, str):
        return False
    normalized = value.replace("/", "\\").casefold()
    return "\\.gemlogin\\profile\\profiles\\" in normalized


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LocalChromiumError(f"Không đọc được {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LocalChromiumError(f"Dữ liệu Chromium không hợp lệ: {path}")
    return value


def _gemlogin_copy_expected_major(user_data_dir: Path) -> int | None:
    """Return the browser major embedded by GemLogin, or None for Dyna profiles."""
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
    normalized = str(executable_path).replace("/", "\\")
    match = re.search(r"(?i)\\browser\\(\d+)\\", normalized)
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
            "Không xác định được phiên bản executable cho bản copy GemLogin; "
            f"Profile này yêu cầu Iron/Chromium {expected_major}."
        )
    if actual_major != expected_major:
        raise LocalChromiumError(
            f"Bản copy GemLogin yêu cầu Iron/Chromium {expected_major} nhưng executable "
            f"đang là phiên bản {actual_major}. Dyna từ chối mở vì dùng sai runtime có "
            "thể làm Chrome xóa cookie phiên đăng nhập."
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
        raise LocalChromiumError("Đường dẫn browser và profile phải là đường dẫn tuyệt đối.")
    if _is_inside_original_gemlogin_profiles(user_data_dir):
        raise LocalChromiumError(
            "Từ chối mở profile gốc của GemLogin; hãy cấu hình một bản copy nằm ngoài .gemlogin."
        )
    if not user_data_dir.is_dir():
        raise LocalChromiumError(f"User Data Directory không tồn tại: {user_data_dir}")
    if not executable_path.is_file():
        raise LocalChromiumError(f"Chromium executable không tồn tại: {executable_path}")

    profile_directory = str(browser.get("profile_directory") or "Default").strip()
    if not profile_directory or any(char in profile_directory for char in ("/", "\\", ":")):
        raise LocalChromiumError("Tên Chromium profile directory không hợp lệ.")
    required = (
        user_data_dir / "Local State",
        user_data_dir / profile_directory,
        user_data_dir / profile_directory / "Preferences",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise LocalChromiumError(
            "User Data Directory chưa hoàn chỉnh; thiếu: " + ", ".join(missing)
        )

    _ensure_gemlogin_runtime_compatible(user_data_dir, executable_path)

    local_state = _read_json(user_data_dir / "Local State")
    if not allow_original_references and _contains_original_profile_reference(local_state):
        raise LocalChromiumError(
            "Bản copy còn tham chiếu tuyệt đối về profile gốc GemLogin. "
            "Hãy chạy tools/gemlogin_profile_probe.py --rewrite-copy-paths trước."
        )

    try:
        launch_timeout_ms = max(5_000, int(browser.get("launch_timeout_ms") or 60_000))
    except (TypeError, ValueError) as exc:
        raise LocalChromiumError("Thời gian chờ mở Chromium không hợp lệ.") from exc
    proxy_data = dict(browser.get("proxy") or {})
    proxy = None
    if bool(proxy_data.get("enabled", False)):
        server = str(proxy_data.get("server") or "").strip()
        if not server:
            raise LocalChromiumError("Proxy đã bật nhưng chưa có địa chỉ máy chủ.")
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


def resolve_local_chromium_config(profile_config: dict[str, Any]) -> LocalChromiumConfig:
    return _resolve_local_chromium_config(
        profile_config,
        allow_original_references=False,
    )


def _profile_lock(key: str) -> threading.RLock:
    with _PROFILE_LOCKS_GUARD:
        return _PROFILE_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _cross_process_profile_lock(key: str, timeout_ms: int = 30_000):
    if os.name != "nt":
        yield
        return

    from ctypes import wintypes

    mutex_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    mutex_name = f"Local\\Dyna.LocalChromium.{mutex_hash}"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create_mutex.restype = wintypes.HANDLE
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait_for_single_object.restype = wintypes.DWORD
    release_mutex = kernel32.ReleaseMutex
    release_mutex.argtypes = (wintypes.HANDLE,)
    release_mutex.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = create_mutex(None, False, mutex_name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        result = wait_for_single_object(handle, max(0, int(timeout_ms)))
        if result not in (0x00000000, 0x00000080):
            raise LocalChromiumError(
                "Profile local đang được một tiến trình Dyna khác sử dụng."
            )
        acquired = True
        yield
    finally:
        if acquired:
            release_mutex(handle)
        close_handle(handle)


def _root_lock_artifacts(config: LocalChromiumConfig) -> list[Path]:
    return [
        config.user_data_dir / name
        for name in ROOT_LOCK_NAMES
        if (config.user_data_dir / name).exists() or (config.user_data_dir / name).is_symlink()
    ]


def _browser_processes_using(user_data_dir: Path) -> list[psutil.Process]:
    target = normalized_path(user_data_dir)
    matches: list[psutil.Process] = []
    for process in psutil.process_iter(("pid", "name", "cmdline")):
        try:
            name = str(process.info.get("name") or "").casefold()
            if name not in BROWSER_PROCESS_NAMES:
                continue
            command = [str(item) for item in (process.info.get("cmdline") or ())]
            configured_dirs: list[str] = []
            for index, argument in enumerate(command):
                if argument.casefold().startswith("--user-data-dir="):
                    configured_dirs.append(argument.split("=", 1)[1].strip('"'))
                elif argument.casefold() == "--user-data-dir" and index + 1 < len(command):
                    configured_dirs.append(command[index + 1].strip('"'))
            if any(normalized_path(value) == target for value in configured_dirs if value):
                matches.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return matches


def ensure_local_profile_unlocked(config: LocalChromiumConfig) -> None:
    locks = _root_lock_artifacts(config)
    processes = _browser_processes_using(config.user_data_dir)
    if not locks and not processes:
        return
    details: list[str] = []
    if locks:
        details.append("lock=" + ",".join(path.name for path in locks))
    if processes:
        details.append("pid=" + ",".join(str(process.pid) for process in processes))
    raise LocalChromiumError(
        "Chromium profile đang được sử dụng hoặc chưa đóng sạch ("
        + "; ".join(details)
        + "). Dyna không tự xóa file khóa."
    )


def quarantine_stale_profile_locks(config: LocalChromiumConfig) -> dict[str, Any]:
    """Move crash leftovers aside only when no live browser owns the profile."""
    lock = _profile_lock(config.key)
    with lock, _cross_process_profile_lock(config.key):
        processes = _browser_processes_using(config.user_data_dir)
        if processes:
            ensure_local_profile_unlocked(config)
        artifacts = _root_lock_artifacts(config)
        if not artifacts:
            return {"changed": 0, "recovery_path": "", "locks": []}

        recovery_path = (
            config.user_data_dir
            / ".dyna-recovery"
            / "stale-locks"
            / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        )
        recovery_path.mkdir(parents=True, exist_ok=False)
        moved: list[str] = []
        try:
            for artifact in artifacts:
                destination = recovery_path / artifact.name
                os.replace(artifact, destination)
                moved.append(artifact.name)
        except Exception as exc:
            for name in reversed(moved):
                source = recovery_path / name
                if source.exists() or source.is_symlink():
                    try:
                        os.replace(source, config.user_data_dir / name)
                    except OSError:
                        pass
            raise LocalChromiumError(
                f"Không thể cách ly file khóa Chromium còn sót lại: {exc}"
            ) from exc

    LOGGER.warning(
        "Đã cách ly khóa Chromium cũ của %s vào %s: %s",
        config.user_data_dir,
        recovery_path,
        ", ".join(moved),
    )
    return {
        "changed": len(moved),
        "recovery_path": str(recovery_path),
        "locks": moved,
    }


def inspect_local_chromium_profile(
    profile_config: dict[str, Any],
    *,
    repair_stale_locks: bool = False,
) -> dict[str, Any]:
    """Return an actionable preflight result without launching Chromium."""
    checked_at = datetime.now().isoformat(timespec="seconds")
    try:
        config = _resolve_local_chromium_config(
            profile_config,
            allow_original_references=True,
        )
    except Exception as exc:
        summary = summarize_local_chromium_error(exc)
        return {
            "status": "invalid",
            "code": classify_local_chromium_error(exc),
            "ready": False,
            "message": summary,
            "suggested_action": local_chromium_recovery_action(exc),
            "locks": [],
            "pids": [],
            "repaired": False,
            "recovery_path": "",
            "checked_at": checked_at,
        }

    processes = _browser_processes_using(config.user_data_dir)
    locks = _root_lock_artifacts(config)
    if processes:
        detail = LocalChromiumError(
            "Chromium profile đang được sử dụng bởi PID "
            + ", ".join(str(process.pid) for process in processes)
            + "."
        )
        return {
            "status": "in_use",
            "code": "profile_in_use",
            "ready": False,
            "message": str(detail),
            "suggested_action": local_chromium_recovery_action(detail),
            "locks": [path.name for path in locks],
            "pids": [process.pid for process in processes],
            "repaired": False,
            "recovery_path": "",
            "checked_at": checked_at,
        }

    repaired = {"changed": 0, "recovery_path": "", "locks": []}
    if locks and repair_stale_locks:
        try:
            repaired = quarantine_stale_profile_locks(config)
            locks = _root_lock_artifacts(config)
        except Exception as exc:
            return {
                "status": "blocked",
                "code": "stale_locks",
                "ready": False,
                "message": summarize_local_chromium_error(exc),
                "suggested_action": "Đóng Dyna, kiểm tra quyền thư mục Profile rồi thử lại.",
                "locks": [path.name for path in locks],
                "pids": [],
                "repaired": False,
                "recovery_path": "",
                "checked_at": checked_at,
            }

    if locks:
        return {
            "status": "blocked",
            "code": "stale_locks",
            "ready": False,
            "message": "Profile còn file khóa sau lần Chromium đóng không sạch.",
            "suggested_action": "Bấm Kiểm tra & sửa để Dyna cách ly các file khóa cũ.",
            "locks": [path.name for path in locks],
            "pids": [],
            "repaired": False,
            "recovery_path": "",
            "checked_at": checked_at,
        }

    try:
        prepare_local_chromium_copy(profile_config)
        config = resolve_local_chromium_config(profile_config)
    except Exception as exc:
        summary = summarize_local_chromium_error(exc)
        return {
            "status": "invalid",
            "code": classify_local_chromium_error(exc),
            "ready": False,
            "message": summary,
            "suggested_action": local_chromium_recovery_action(exc),
            "locks": [],
            "pids": [],
            "repaired": bool(repaired.get("changed")),
            "recovery_path": str(repaired.get("recovery_path") or ""),
            "checked_at": checked_at,
        }

    if not os.access(config.user_data_dir, os.R_OK | os.W_OK):
        return {
            "status": "blocked",
            "code": "permission_denied",
            "ready": False,
            "message": "Dyna không có quyền đọc và ghi thư mục Profile Chromium.",
            "suggested_action": "Cấp quyền ghi cho thư mục Profile hoặc chọn một bản sao khác.",
            "locks": [],
            "pids": [],
            "repaired": False,
            "recovery_path": "",
            "checked_at": checked_at,
        }

    changed = int(repaired.get("changed") or 0)
    return {
        "status": "recovered" if changed else "ready",
        "code": "stale_locks_repaired" if changed else "ready",
        "ready": True,
        "message": (
            f"Đã cách ly {changed} file khóa cũ; Profile sẵn sàng mở."
            if changed
            else "Cấu hình và thư mục Profile Chromium hợp lệ."
        ),
        "suggested_action": "Có thể mở đăng nhập hoặc chạy Profile.",
        "locks": list(repaired.get("locks") or []),
        "pids": [],
        "repaired": bool(changed),
        "recovery_path": str(repaired.get("recovery_path") or ""),
        "checked_at": checked_at,
    }


def prepare_local_chromium_copy(profile_config: dict[str, Any]) -> dict[str, Any]:
    """Safely detach known absolute GemLogin paths inside a configured copy.

    Only ``Local State`` in the copied User Data Directory is changed. The
    original GemLogin tree is always refused, the copy must be unlocked, and a
    timestamped backup is written before the atomic replacement.
    """
    config = _resolve_local_chromium_config(
        profile_config,
        allow_original_references=True,
    )
    local_state_path = config.user_data_dir / "Local State"
    local_state = _read_json(local_state_path)
    if not _contains_original_profile_reference(local_state):
        return {"changed": 0, "backup_path": ""}

    folder_name = re.escape(config.user_data_dir.name)
    original_prefix = re.compile(
        rf"(?i)^[A-Z]:\\.*?\\\.gemlogin\\profile\\profiles\\{folder_name}(?=\\|$)"
    )
    changed_paths: list[str] = []

    def rewrite(value: Any, prefix: str = "") -> Any:
        if isinstance(value, dict):
            return {
                key: rewrite(nested, f"{prefix}.{key}" if prefix else str(key))
                for key, nested in value.items()
            }
        if isinstance(value, list):
            return [
                rewrite(nested, f"{prefix}[{index}]")
                for index, nested in enumerate(value)
            ]
        if isinstance(value, str):
            match = original_prefix.match(value.replace("/", "\\"))
            if match:
                changed_paths.append(prefix)
                return f"{config.user_data_dir}{value.replace('/', '\\')[match.end():]}"
        return value

    rewritten = rewrite(local_state)
    if not changed_paths:
        raise LocalChromiumError(
            "Local State còn tham chiếu GemLogin nhưng không khớp Profile copy hiện tại; "
            "Dyna không tự sửa để tránh trỏ nhầm dữ liệu."
        )

    lock = _profile_lock(config.key)
    with lock, _cross_process_profile_lock(config.key):
        ensure_local_profile_unlocked(config)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = config.user_data_dir / f"Local State.dyna-backup-{timestamp}.json"
        temporary_path = config.user_data_dir / f"Local State.dyna-{os.getpid()}.tmp"
        try:
            shutil.copy2(local_state_path, backup_path)
            temporary_path.write_text(
                json.dumps(rewritten, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary_path, local_state_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    resolve_local_chromium_config(profile_config)
    LOGGER.info(
        "Đã tách %s tham chiếu GemLogin khỏi bản copy %s; backup=%s",
        len(changed_paths),
        config.user_data_dir,
        backup_path,
    )
    return {
        "changed": len(changed_paths),
        "backup_path": str(backup_path),
        "paths": changed_paths,
    }


def wait_for_local_profile_unlocked(
    config: LocalChromiumConfig,
    timeout_seconds: float = 8,
) -> bool:
    """Wait for Chromium's normal shutdown; never delete lock artifacts or kill a process."""
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        if not _root_lock_artifacts(config) and not _browser_processes_using(config.user_data_dir):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def _record_health(
    key: str,
    status: str,
    error: object = "",
    **details: Any,
) -> None:
    with _PROFILE_HEALTH_GUARD:
        previous = _PROFILE_HEALTH.get(key, {})
        recovery_count = int(previous.get("recovery_count") or 0)
        if status == "recovered":
            recovery_count += 1
        _PROFILE_HEALTH[key] = {
            "provider": "local_chromium",
            "profile_key": key,
            "status": status,
            "last_error": str(error or ""),
            "recovery_count": recovery_count,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "error_code": classify_local_chromium_error(error) if error else "",
            "suggested_action": local_chromium_recovery_action(error) if error else "",
            **details,
        }


def get_local_chromium_health(profile_config: dict[str, Any]) -> dict[str, Any]:
    try:
        config = resolve_local_chromium_config(profile_config)
        key = config.key
    except Exception as exc:
        return {
            "provider": "local_chromium",
            "profile_key": "",
            "status": "invalid",
            "last_error": str(exc),
            "recovery_count": 0,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }
    with _PROFILE_HEALTH_GUARD:
        return dict(
            _PROFILE_HEALTH.get(
                key,
                {
                    "provider": "local_chromium",
                    "profile_key": key,
                    "status": "ready",
                    "last_error": "",
                    "recovery_count": 0,
                    "checked_at": "",
                },
            )
        )


def get_local_chromium_processes(profile_config: dict[str, Any]) -> list[psutil.Process]:
    """Return only browser roots whose command line names this copied User Data Dir."""
    config = resolve_local_chromium_config(profile_config)
    return _browser_processes_using(config.user_data_dir)


def _launch_args(
    config: LocalChromiumConfig,
    *,
    safe_mode: bool = False,
    visible: bool = False,
    resource_saving: bool = False,
) -> list[str]:
    args = [
        f"--profile-directory={config.profile_directory}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
    ]
    if safe_mode:
        args.extend(
            (
                "--disable-extensions",
                "--disable-gpu",
                "--disable-background-mode",
                "--disable-features=OptimizationHints",
            )
        )
    elif resource_saving and not visible:
        args.extend(
            (
                "--disable-extensions",
                "--disable-background-mode",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-notifications",
                "--disable-sync",
                "--mute-audio",
                "--no-service-autorun",
                "--renderer-process-limit=2",
                "--force-prefers-reduced-motion",
                "--disable-features=OptimizationHints,MediaRouter,Translate,AutofillServerCommunication",
            )
        )
    if config.background and (safe_mode or not config.headless) and not visible:
        args.extend(("--window-position=-32000,-32000", "--window-size=1280,720"))
    elif visible:
        args.append("--window-size=1280,900")
    return args


def local_chromium_launch_options(
    config: LocalChromiumConfig,
    *,
    safe_mode: bool = False,
    visible: bool = False,
    resource_saving: bool = False,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "user_data_dir": str(config.user_data_dir),
        "executable_path": str(config.executable_path),
        "headless": False if safe_mode or visible else config.headless,
        "no_viewport": True,
        "accept_downloads": True,
        "timeout": config.launch_timeout_ms,
        "args": _launch_args(
            config,
            safe_mode=safe_mode,
            visible=visible,
            resource_saving=resource_saving,
        ),
    }
    if not safe_mode and not resource_saving:
        options["ignore_default_args"] = ["--disable-extensions"]
    if config.proxy is not None:
        # Chromium is launched with a fixed per-Profile proxy. If the proxy is
        # unavailable, navigation fails instead of silently using the direct IP.
        options["proxy"] = config.proxy.as_playwright_options()
    return options


@contextmanager
def connected_local_chromium_profile(
    profile_config: dict[str, Any],
    *,
    attempts: int = 2,
    retry_delay_seconds: float = 1,
    resource_saving: bool = False,
):
    inspection = inspect_local_chromium_profile(
        profile_config,
        repair_stale_locks=True,
    )
    if not inspection.get("ready"):
        raise LocalChromiumError(
            f"{inspection.get('message')} {inspection.get('suggested_action')}".strip()
        )
    config = resolve_local_chromium_config(profile_config)
    max_attempts = max(1, int(attempts))
    delay = max(0.0, float(retry_delay_seconds))
    lock = _profile_lock(config.key)

    with lock, _cross_process_profile_lock(config.key):
        if not wait_for_local_profile_unlocked(config):
            ensure_local_profile_unlocked(config)
        last_error: Exception | None = None
        manager = None
        playwright = None
        context = None
        browser = None
        for attempt in range(1, max_attempts + 1):
            manager = sync_playwright()
            safe_mode = attempt > 1
            try:
                if attempt > 1:
                    retry_inspection = inspect_local_chromium_profile(
                        profile_config,
                        repair_stale_locks=True,
                    )
                    if not retry_inspection.get("ready"):
                        raise LocalChromiumError(
                            f"{retry_inspection.get('message')} "
                            f"{retry_inspection.get('suggested_action')}".strip()
                        )
                _record_health(
                    config.key,
                    "recovering" if safe_mode else "starting",
                    safe_mode=safe_mode,
                    attempt=attempt,
                    max_attempts=max_attempts,
                )
                playwright = manager.start()
                context = playwright.chromium.launch_persistent_context(
                    **local_chromium_launch_options(
                        config,
                        safe_mode=safe_mode,
                        resource_saving=resource_saving,
                    )
                )
                browser = LocalPersistentBrowser(context)
                _record_health(
                    config.key,
                    "recovered" if safe_mode else "healthy",
                    safe_mode=safe_mode,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    recovery_action="safe_mode" if safe_mode else "",
                )
                if safe_mode:
                    LOGGER.warning(
                        "Local Chromium %s đã mở lại thành công ở chế độ an toàn.",
                        config.user_data_dir,
                    )
                break
            except Exception as exc:
                last_error = exc
                error_summary = summarize_local_chromium_error(exc)
                _record_health(
                    config.key,
                    "unhealthy",
                    error_summary,
                    safe_mode=safe_mode,
                    attempt=attempt,
                    max_attempts=max_attempts,
                )
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass
                try:
                    if playwright is not None:
                        playwright.stop()
                    else:
                        manager.__exit__(None, None, None)
                except Exception:
                    pass
                manager = None
                playwright = None
                context = None
                browser = None
                if attempt >= max_attempts:
                    break
                LOGGER.warning(
                    "Mở/kết nối Local Chromium thất bại ở lần %s/%s: %s",
                    attempt,
                    max_attempts,
                    error_summary,
                )
                if delay:
                    time.sleep(delay)

        if browser is None or context is None:
            error_summary = summarize_local_chromium_error(last_error)
            raise LocalChromiumError(
                f"Không thể mở Chromium profile local sau {max_attempts} lần: {error_summary}"
            ) from last_error

        try:
            yield browser
        finally:
            browser.mark_closed()
            try:
                context.close()
            except Exception:
                pass
            if not wait_for_local_profile_unlocked(config, timeout_seconds=10):
                LOGGER.warning(
                    "Local Chromium profile vẫn còn lock/tiến trình sau khi đóng sạch: %s",
                    config.user_data_dir,
                )
            try:
                if playwright is not None:
                    playwright.stop()
                elif manager is not None:
                    manager.__exit__(None, None, None)
            except Exception:
                pass


def close_local_chromium_profile(profile_config: dict[str, Any]) -> None:
    config = resolve_local_chromium_config(profile_config)
    processes = _browser_processes_using(config.user_data_dir)
    if not processes:
        _record_health(config.key, "closed")
        return

    matching_pids = {process.pid for process in processes}
    roots: list[psutil.Process] = []
    for process in processes:
        try:
            if process.ppid() not in matching_pids:
                roots.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    roots = roots or processes
    for process in roots:
        try:
            process.terminate()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    _, alive = psutil.wait_procs(roots, timeout=5)
    for process in alive:
        try:
            process.kill()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    _record_health(config.key, "closed")


def create_local_background_page(context):
    try:
        return context.new_page()
    except Exception as exc:
        raise LocalChromiumError("Không thể tạo tab automation trong local Chromium.") from exc
