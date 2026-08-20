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
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from services.browser.local_chromium_config import (
    LocalChromiumConfig,
    LocalChromiumError,
    _contains_original_profile_reference,
    _read_json,
    _resolve_local_chromium_config,
    classify_local_chromium_error,
    local_chromium_recovery_action,
    normalized_path,
    resolve_local_chromium_config,
    summarize_local_chromium_error,
)


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
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create_mutex.restype = wintypes.HANDLE
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait_for_single_object.restype = wintypes.DWORD
    release_mutex = kernel32.ReleaseMutex
    release_mutex.argtypes = (wintypes.HANDLE,)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)

    handle = create_mutex(
        None,
        False,
        f"Local\\Dyna.LocalChromium.{mutex_hash}",
    )
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
        if (config.user_data_dir / name).exists()
        or (config.user_data_dir / name).is_symlink()
    ]


def _browser_processes_using(user_data_dir: Path) -> list[psutil.Process]:
    target = normalized_path(user_data_dir)
    matches: list[psutil.Process] = []
    for process in psutil.process_iter(("pid", "name", "cmdline")):
        try:
            if str(process.info.get("name") or "").casefold() not in BROWSER_PROCESS_NAMES:
                continue
            command = [str(item) for item in (process.info.get("cmdline") or ())]
            configured_dirs: list[str] = []
            for index, argument in enumerate(command):
                if argument.casefold().startswith("--user-data-dir="):
                    configured_dirs.append(argument.split("=", 1)[1].strip('"'))
                elif (
                    argument.casefold() == "--user-data-dir"
                    and index + 1 < len(command)
                ):
                    configured_dirs.append(command[index + 1].strip('"'))
            if any(
                normalized_path(value) == target
                for value in configured_dirs
                if value
            ):
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
        + ")."
    )


def quarantine_stale_profile_locks(
    config: LocalChromiumConfig,
) -> dict[str, Any]:
    with _profile_lock(config.key), _cross_process_profile_lock(config.key):
        if _browser_processes_using(config.user_data_dir):
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
                os.replace(artifact, recovery_path / artifact.name)
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
                f"Không thể cách ly file khóa Chromium: {exc}"
            ) from exc
    return {
        "changed": len(moved),
        "recovery_path": str(recovery_path),
        "locks": moved,
    }


def prepare_local_chromium_copy(
    profile_config: dict[str, Any],
) -> dict[str, Any]:
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
            normalized = value.replace("/", "\\")
            match = original_prefix.match(normalized)
            if match:
                changed_paths.append(prefix)
                return f"{config.user_data_dir}{normalized[match.end():]}"
        return value

    rewritten = rewrite(local_state)
    if not changed_paths:
        raise LocalChromiumError(
            "Local State còn tham chiếu GemLogin nhưng không khớp Profile copy."
        )

    with _profile_lock(config.key), _cross_process_profile_lock(config.key):
        ensure_local_profile_unlocked(config)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = (
            config.user_data_dir / f"Local State.dyna-backup-{timestamp}.json"
        )
        temporary_path = (
            config.user_data_dir / f"Local State.dyna-{os.getpid()}.tmp"
        )
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
    return {
        "changed": len(changed_paths),
        "backup_path": str(backup_path),
        "paths": changed_paths,
    }


def inspect_local_chromium_profile(
    profile_config: dict[str, Any],
    *,
    repair_stale_locks: bool = False,
) -> dict[str, Any]:
    checked_at = datetime.now().isoformat(timespec="seconds")
    try:
        config = _resolve_local_chromium_config(
            profile_config,
            allow_original_references=True,
        )
    except Exception as exc:
        return {
            "status": "invalid",
            "code": classify_local_chromium_error(exc),
            "ready": False,
            "message": summarize_local_chromium_error(exc),
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
        return {
            "status": "in_use",
            "code": "profile_in_use",
            "ready": False,
            "message": "Chromium profile đang được sử dụng.",
            "suggested_action": "Đóng Chromium rồi thử lại.",
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
                "suggested_action": "Kiểm tra quyền thư mục Profile rồi thử lại.",
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
            "suggested_action": "Bấm Kiểm tra & sửa.",
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
        return {
            "status": "invalid",
            "code": classify_local_chromium_error(exc),
            "ready": False,
            "message": summarize_local_chromium_error(exc),
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
            "message": "Dyna không có quyền đọc và ghi thư mục Profile.",
            "suggested_action": "Cấp quyền ghi hoặc chọn bản sao khác.",
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
        "message": "Profile Chromium sẵn sàng.",
        "suggested_action": "Có thể mở đăng nhập hoặc chạy Profile.",
        "locks": list(repaired.get("locks") or []),
        "pids": [],
        "repaired": bool(changed),
        "recovery_path": str(repaired.get("recovery_path") or ""),
        "checked_at": checked_at,
    }


def wait_for_local_profile_unlocked(
    config: LocalChromiumConfig,
    timeout_seconds: float = 8,
) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        if not _root_lock_artifacts(config) and not _browser_processes_using(
            config.user_data_dir
        ):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)
