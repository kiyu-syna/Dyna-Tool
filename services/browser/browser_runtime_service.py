from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import core.config as config
from core.runtime_paths import state_dir
from playwright.sync_api import sync_playwright


MANIFEST_NAME = "dyna-runtime.json"
MANIFEST_FORMAT = 1
EXCLUDED_SOURCE_FILES = {"gemlogindriver.exe", MANIFEST_NAME}
_INSTALL_LOCK = threading.RLock()


class BrowserRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserRuntimeInfo:
    runtime_id: str
    root_dir: str
    executable_path: str
    source_executable: str
    installed_at: str
    file_count: int
    size_bytes: int
    executable_sha256: str
    reused: bool = False
    valid: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_browser_runtime_root() -> Path:
    local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local_app_data:
        return Path(local_app_data) / "Dyna" / "browser-runtimes"
    return state_dir() / "browser-runtimes"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _runtime_id(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if not normalized or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", normalized):
        raise BrowserRuntimeError(
            "Runtime ID chỉ được chứa chữ thường, số, dấu chấm, gạch ngang hoặc gạch dưới."
        )
    return normalized


def suggested_runtime_id(source_executable: str | os.PathLike) -> str:
    executable = Path(source_executable)
    for parent in executable.parents:
        match = re.fullmatch(r"chromium-(\d+)", parent.name.casefold())
        if match:
            return f"chromium-{match.group(1)}"
    parent_version = executable.parent.parent.name.strip()
    suffix = parent_version if parent_version.isdigit() else "custom"
    return f"iron-{suffix}"


def _validate_source_executable(value: str | os.PathLike) -> Path:
    executable = Path(str(value or "").strip())
    if not executable.is_absolute():
        raise BrowserRuntimeError("Đường dẫn browser nguồn phải là đường dẫn tuyệt đối.")
    if not executable.is_file():
        raise BrowserRuntimeError(f"Không tìm thấy browser executable nguồn: {executable}")
    if executable.suffix.casefold() != ".exe":
        raise BrowserRuntimeError("Browser executable nguồn phải là file .exe.")
    source_root = executable.parent
    root_files = {item.name for item in source_root.iterdir() if item.is_file()}
    version_dirs = [
        child
        for child in source_root.iterdir()
        if child.is_dir() and re.fullmatch(r"\d+(?:\.\d+){2,3}", child.name)
    ]
    required_names = {"chrome.dll", "icudtl.dat", "resources.pak"}
    flat_runtime = required_names.issubset(root_files)
    if not version_dirs and not flat_runtime:
        raise BrowserRuntimeError(
            "Thư mục nguồn không có bộ Chromium hoàn chỉnh."
        )
    if not flat_runtime and not any(
        required_names.issubset({item.name for item in version_dir.iterdir() if item.is_file()})
        for version_dir in version_dirs
    ):
        raise BrowserRuntimeError("Bộ Chromium nguồn thiếu chrome.dll, icudtl.dat hoặc resources.pak.")
    return executable.resolve()


def _playwright_chromium_executable() -> Path:
    manager = sync_playwright()
    playwright = manager.start()
    try:
        return Path(playwright.chromium.executable_path)
    finally:
        playwright.stop()


def install_playwright_chromium_runtime(
    *,
    destination_root: str | os.PathLike | None = None,
    download_timeout_seconds: int = 1200,
) -> BrowserRuntimeInfo:
    executable = _playwright_chromium_executable()
    if not executable.is_file():
        try:
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True,
                capture_output=True,
                text=True,
                timeout=max(60, int(download_timeout_seconds)),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            details = str(getattr(exc, "stderr", "") or exc).strip()
            raise BrowserRuntimeError(f"Không thể tải Chromium qua Playwright: {details}") from exc
        executable = _playwright_chromium_executable()
    if not executable.is_file():
        raise BrowserRuntimeError("Playwright báo đã cài nhưng không tìm thấy Chromium executable.")
    return install_browser_runtime(
        executable,
        runtime_id=suggested_runtime_id(executable),
        destination_root=destination_root,
    )


def _copy_ignore(source_root: Path):
    normalized_root = os.path.normcase(str(source_root.resolve()))

    def ignore(directory: str, names: list[str]) -> set[str]:
        if os.path.normcase(str(Path(directory).resolve())) != normalized_root:
            return set()
        excluded = {name for name in names if name.casefold() in EXCLUDED_SOURCE_FILES}
        return excluded

    return ignore


def _build_file_records(root: Path) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    total_size = 0
    for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        size = path.stat().st_size
        total_size += size
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": size,
                "sha256": _sha256(path),
            }
        )
    return records, total_size


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _load_manifest(root: Path) -> dict[str, Any]:
    path = root / MANIFEST_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BrowserRuntimeError(f"Không đọc được manifest runtime: {path}") from exc
    if not isinstance(payload, dict) or payload.get("format") != MANIFEST_FORMAT:
        raise BrowserRuntimeError(f"Manifest runtime không hợp lệ: {path}")
    return payload


def verify_browser_runtime(
    executable_path: str | os.PathLike,
    *,
    verify_hashes: bool = True,
) -> BrowserRuntimeInfo:
    executable = Path(str(executable_path or "").strip())
    root = executable.parent
    manifest = _load_manifest(root)
    expected_executable = root / str(manifest.get("executable_relative_path") or "")
    if expected_executable.resolve() != executable.resolve() or not executable.is_file():
        raise BrowserRuntimeError("Executable không khớp manifest runtime của Dyna.")

    records = manifest.get("files") or []
    if not isinstance(records, list) or not records:
        raise BrowserRuntimeError("Manifest runtime không có danh sách file.")
    for record in records:
        relative_path = str((record or {}).get("path") or "")
        candidate = root / Path(relative_path)
        try:
            candidate.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise BrowserRuntimeError("Manifest runtime chứa đường dẫn không an toàn.") from exc
        if not candidate.is_file() or candidate.stat().st_size != int(record.get("size") or -1):
            raise BrowserRuntimeError(f"Runtime thiếu hoặc sai kích thước file: {relative_path}")
        if verify_hashes and _sha256(candidate) != str(record.get("sha256") or ""):
            raise BrowserRuntimeError(f"Runtime sai SHA-256: {relative_path}")

    return BrowserRuntimeInfo(
        runtime_id=str(manifest.get("runtime_id") or root.name),
        root_dir=str(root.resolve()),
        executable_path=str(executable.resolve()),
        source_executable=str(manifest.get("source_executable") or ""),
        installed_at=str(manifest.get("installed_at") or ""),
        file_count=len(records),
        size_bytes=sum(int(record.get("size") or 0) for record in records),
        executable_sha256=str(manifest.get("executable_sha256") or ""),
        reused=False,
        valid=True,
    )


def install_browser_runtime(
    source_executable: str | os.PathLike,
    *,
    runtime_id: str = "",
    destination_root: str | os.PathLike | None = None,
) -> BrowserRuntimeInfo:
    source = _validate_source_executable(source_executable)
    source_root = source.parent
    destination_base = Path(destination_root or default_browser_runtime_root()).resolve()
    if (source_root / MANIFEST_NAME).is_file():
        existing = verify_browser_runtime(source)
        try:
            source_root.resolve().relative_to(destination_base)
        except ValueError as exc:
            raise BrowserRuntimeError(
                "Runtime nguồn có manifest Dyna nhưng nằm ngoài thư mục runtime đang quản lý."
            ) from exc
        return BrowserRuntimeInfo(**{**existing.to_dict(), "reused": True})

    runtime_id = _runtime_id(runtime_id or suggested_runtime_id(source))
    target = destination_base / runtime_id
    target_executable = target / source.name
    source_digest = _sha256(source)

    try:
        source.relative_to(destination_base)
    except ValueError:
        pass
    else:
        if target.resolve() == source_root.resolve():
            return verify_browser_runtime(source)
        raise BrowserRuntimeError("Nguồn runtime không được nằm bên trong thư mục cài runtime của Dyna.")

    with _INSTALL_LOCK:
        if target.exists():
            existing = verify_browser_runtime(target_executable)
            manifest = _load_manifest(target)
            if str(manifest.get("source_executable_sha256") or "") != source_digest:
                raise BrowserRuntimeError(
                    f"Runtime {runtime_id} đã tồn tại nhưng không cùng executable nguồn."
                )
            return BrowserRuntimeInfo(**{**existing.to_dict(), "reused": True})

        destination_base.mkdir(parents=True, exist_ok=True)
        staging = destination_base / f".{runtime_id}.installing-{uuid.uuid4().hex}"
        try:
            shutil.copytree(source_root, staging, ignore=_copy_ignore(source_root))
            copied_executable = staging / source.name
            _validate_source_executable(copied_executable)
            records, total_size = _build_file_records(staging)
            copied_executable_record = next(
                (
                    record
                    for record in records
                    if record["path"].casefold() == source.name.casefold()
                ),
                None,
            )
            if copied_executable_record is None:
                raise BrowserRuntimeError("Bản runtime copy thiếu browser executable.")
            installed_at = datetime.now().isoformat(timespec="seconds")
            manifest = {
                "format": MANIFEST_FORMAT,
                "runtime_id": runtime_id,
                "installed_at": installed_at,
                "source_executable": str(source),
                "source_executable_sha256": source_digest,
                "executable_relative_path": source.name,
                "executable_sha256": copied_executable_record["sha256"],
                "file_count": len(records),
                "size_bytes": total_size,
                "excluded_source_files": sorted(EXCLUDED_SOURCE_FILES),
                "files": records,
            }
            _write_manifest(staging / MANIFEST_NAME, manifest)
            os.replace(staging, target)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise

    return verify_browser_runtime(target_executable)


def list_browser_runtimes(
    destination_root: str | os.PathLike | None = None,
) -> list[dict[str, Any]]:
    root = Path(destination_root or default_browser_runtime_root())
    if not root.is_dir():
        return []
    runtimes: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        try:
            manifest = _load_manifest(child)
            executable = child / str(manifest.get("executable_relative_path") or "")
            info = verify_browser_runtime(executable, verify_hashes=False)
            runtimes.append(info.to_dict())
        except Exception as exc:
            runtimes.append(
                {
                    "runtime_id": child.name,
                    "root_dir": str(child.resolve()),
                    "executable_path": "",
                    "valid": False,
                    "error": str(exc),
                }
            )
    return runtimes
