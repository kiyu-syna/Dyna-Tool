"""Inspect and launch a copied GemLogin Chromium profile with Playwright.

This utility intentionally refuses GemLogin's original profile tree. Chromium
will write lock/session/preference data whenever a persistent context is
opened, so the target must be a disposable or user-approved copy.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psutil
from playwright.sync_api import BrowserContext, Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


LOGGER = logging.getLogger("gemlogin_profile_probe")
ORIGINAL_TREE_PARTS = (".gemlogin", "profile", "profiles")
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


@dataclass(frozen=True)
class SiteCheck:
    name: str
    url: str
    auth_cookie_names: frozenset[str]
    logged_out_selector: str | None = None


SITE_CHECKS = (
    SiteCheck(
        "Facebook",
        "https://www.facebook.com/",
        frozenset({"c_user"}),
        'input[name="email"], input[name="pass"]',
    ),
    SiteCheck(
        "TikTok",
        "https://www.tiktok.com/",
        frozenset({"sessionid", "sessionid_ss", "sid_tt"}),
        '[data-e2e="top-login-button"]',
    ),
    SiteCheck(
        "Douyin",
        "https://www.douyin.com/",
        frozenset({"sessionid", "sessionid_ss", "sid_guard"}),
    ),
)


class ProbeError(RuntimeError):
    """Expected validation or launch failure with a user-facing explanation."""


def configure_logging(log_file: Path | None) -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )


def normalized_windows_path(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(path))).rstrip("\\/")


def is_original_gemlogin_tree(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.resolve().parts)
    needle = tuple(part.casefold() for part in ORIGINAL_TREE_PARTS)
    return any(parts[index : index + len(needle)] == needle for index in range(len(parts)))


def validate_profile_copy(profile_root: Path) -> None:
    if not profile_root.exists():
        raise ProbeError(f"Profile không tồn tại: {profile_root}")
    if not profile_root.is_dir():
        raise ProbeError(f"Đường dẫn không phải thư mục: {profile_root}")
    if is_original_gemlogin_tree(profile_root):
        raise ProbeError(
            "Từ chối mở profile trong cây .gemlogin\\profile\\profiles. "
            "Hãy dùng một bản sao nằm ngoài thư mục GemLogin."
        )

    required = (
        profile_root / "Local State",
        profile_root / "Default",
        profile_root / "Default" / "Preferences",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ProbeError(
            "Đây không phải Chromium User Data Directory hoàn chỉnh; thiếu: "
            + ", ".join(missing)
        )


def directory_summary(profile_root: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for current_root, _, filenames in os.walk(profile_root):
        current = Path(current_root)
        for filename in filenames:
            try:
                total_bytes += (current / filename).stat().st_size
                file_count += 1
            except OSError as exc:
                LOGGER.warning("Không đọc được metadata %s: %s", current / filename, exc)
    return file_count, total_bytes


def list_top_level(profile_root: Path) -> None:
    LOGGER.info("Cấu trúc cấp gốc:")
    for item in sorted(profile_root.iterdir(), key=lambda entry: entry.name.casefold()):
        kind = "DIR " if item.is_dir() else "FILE"
        size = "-" if item.is_dir() else str(item.stat().st_size)
        LOGGER.info("  %s | %-42s | %s byte", kind, item.name, size)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeError(f"Không đọc được JSON Chromium {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProbeError(f"JSON Chromium không phải object: {path}")
    return value


def read_last_browser(profile_root: Path) -> Path:
    path = profile_root / "Last Browser"
    if not path.is_file():
        raise ProbeError("Không có file 'Last Browser'; cần chỉ định executable Chromium rõ ràng.")
    raw = path.read_bytes()
    try:
        value = raw.decode("utf-16-le").strip("\x00\r\n ")
    except UnicodeDecodeError as exc:
        raise ProbeError(f"Không giải mã được file 'Last Browser': {exc}") from exc
    executable = Path(value)
    if not executable.is_file():
        raise ProbeError(f"Executable ghi trong 'Last Browser' không tồn tại: {executable}")
    return executable


def find_original_profile_references(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    matches: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            matches.extend(find_original_profile_references(nested, path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            matches.extend(find_original_profile_references(nested, f"{prefix}[{index}]"))
    elif isinstance(value, str) and re.search(
        r"(?i)\\\.gemlogin\\profile\\profiles(?:\\|$)", value
    ):
        matches.append((prefix, value))
    return matches


def rewrite_original_references_in_copy(profile_root: Path) -> Path | None:
    local_state_path = profile_root / "Local State"
    local_state = load_json(local_state_path)
    profile_name = re.escape(profile_root.name)
    original_prefix = re.compile(
        rf"(?i)^[A-Z]:\\.*?\\\.gemlogin\\profile\\profiles\\{profile_name}(?=\\|$)"
    )
    changed_paths: list[str] = []

    def rewrite(value: Any, prefix: str = "") -> Any:
        if isinstance(value, dict):
            return {
                key: rewrite(nested, f"{prefix}.{key}" if prefix else str(key))
                for key, nested in value.items()
            }
        if isinstance(value, list):
            return [rewrite(nested, f"{prefix}[{index}]") for index, nested in enumerate(value)]
        if isinstance(value, str):
            match = original_prefix.match(value)
            if match:
                suffix = value[match.end() :]
                replacement = f"{profile_root}{suffix}"
                changed_paths.append(prefix)
                return replacement
        return value

    rewritten = rewrite(local_state)
    if not changed_paths:
        return None

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = profile_root / f"Local State.dyna-probe-backup-{timestamp}.json"
    shutil.copy2(local_state_path, backup_path)
    temporary_path = profile_root / f"Local State.dyna-probe-{os.getpid()}.tmp"
    temporary_path.write_text(
        json.dumps(rewritten, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary_path, local_state_path)
    LOGGER.info("Đã sao lưu Local State: %s", backup_path)
    for path in changed_paths:
        LOGGER.info("Đã chuyển tham chiếu profile gốc sang bản copy tại khóa: %s", path)
    return backup_path


def root_lock_artifacts(profile_root: Path) -> list[Path]:
    return [profile_root / name for name in ROOT_LOCK_NAMES if (profile_root / name).exists()]


def browser_processes_using(profile_root: Path) -> list[tuple[int, str]]:
    target = normalized_windows_path(profile_root)
    matches: list[tuple[int, str]] = []
    for process in psutil.process_iter(("pid", "name", "cmdline")):
        try:
            name = str(process.info.get("name") or "").casefold()
            if name not in BROWSER_PROCESS_NAMES:
                continue
            command = " ".join(process.info.get("cmdline") or ())
            if target in normalized_windows_path(command):
                matches.append((int(process.info["pid"]), name))
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return matches


def ensure_profile_unlocked(profile_root: Path) -> None:
    locks = root_lock_artifacts(profile_root)
    processes = browser_processes_using(profile_root)
    if locks or processes:
        details: list[str] = []
        if locks:
            details.append("lock artifacts=" + ", ".join(path.name for path in locks))
        if processes:
            details.append(
                "browser processes=" + ", ".join(f"{name}:{pid}" for pid, name in processes)
            )
        raise ProbeError(
            "Profile có dấu hiệu đang bị khóa (" + "; ".join(details) + "). "
            "Script không tự xóa file khóa. Hãy đóng tiến trình sở hữu profile rồi thử lại."
        )


def sanitized_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except ValueError:
        return value.split("?", 1)[0].split("#", 1)[0]


def infer_auth_state(
    context: BrowserContext,
    page: Any,
    site: SiteCheck,
) -> tuple[str, list[str], int]:
    cookies = context.cookies(urls=[site.url])
    cookie_names = {str(cookie.get("name") or "") for cookie in cookies}
    matched = sorted(cookie_names.intersection(site.auth_cookie_names))
    final_url = page.url.casefold()
    redirected_to_auth = any(token in final_url for token in ("/login", "/signup", "/checkpoint"))
    logged_out_ui = False
    if site.logged_out_selector:
        try:
            logged_out_ui = page.locator(site.logged_out_selector).first.is_visible(timeout=1_500)
        except (PlaywrightError, PlaywrightTimeoutError):
            logged_out_ui = False

    if redirected_to_auth or logged_out_ui:
        state = "LIKELY_LOGGED_OUT_OR_CHALLENGED"
    elif matched:
        state = "LIKELY_LOGGED_IN"
    else:
        state = "UNDETERMINED"
    return state, matched, len(cookies)


def check_sites(
    context: BrowserContext,
    navigation_timeout_ms: int,
    settle_ms: int,
) -> None:
    page = context.new_page()
    LOGGER.info("URL ngay sau khi tạo page: %s", sanitized_url(page.url))
    for site in SITE_CHECKS:
        LOGGER.info("[%s] Mở %s", site.name, site.url)
        response_status: int | None = None
        navigation_error: str | None = None
        try:
            response = page.goto(
                site.url,
                wait_until="domcontentloaded",
                timeout=navigation_timeout_ms,
            )
            response_status = response.status if response else None
            page.wait_for_timeout(settle_ms)
        except PlaywrightTimeoutError as exc:
            navigation_error = f"navigation timeout: {exc}"
        except PlaywrightError as exc:
            navigation_error = f"navigation error: {exc}"

        LOGGER.info("[%s] URL hiện tại: %s", site.name, sanitized_url(page.url))
        LOGGER.info("[%s] HTTP status: %s", site.name, response_status)
        if navigation_error:
            LOGGER.warning("[%s] %s", site.name, navigation_error)
        try:
            state, matched, cookie_count = infer_auth_state(context, page, site)
            LOGGER.info("[%s] Session: %s", site.name, state)
            LOGGER.info("[%s] Tổng cookie miền: %s", site.name, cookie_count)
            LOGGER.info("[%s] Cookie xác thực tìm thấy (không in value): %s", site.name, matched)
        except PlaywrightError as exc:
            LOGGER.warning("[%s] Không kiểm tra được cookie/session: %s", site.name, exc)


def classify_launch_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.casefold()
    if "processsingleton" in lowered or "user data directory is already in use" in lowered:
        return "Chromium báo User Data Directory đang được tiến trình khác sử dụng."
    if "executable doesn't exist" in lowered or "failed to launch" in lowered:
        return "Executable Chromium không chạy được hoặc không tương thích với Playwright."
    if "target page, context or browser has been closed" in lowered:
        return "Chromium thoát ngay khi Playwright tạo persistent context."
    return "Playwright không mở được persistent context."


def run_probe(args: argparse.Namespace) -> int:
    profile_root = args.profile_copy.resolve()
    LOGGER.info("Bắt đầu kiểm tra bản copy: %s", profile_root)
    validate_profile_copy(profile_root)

    file_count, total_bytes = directory_summary(profile_root)
    LOGGER.info(
        "Kích thước profile: %.3f GiB; %s file",
        total_bytes / (1024**3),
        file_count,
    )
    list_top_level(profile_root)

    local_state = load_json(profile_root / "Local State")
    preferences = load_json(profile_root / "Default" / "Preferences")
    last_version = (profile_root / "Last Version").read_text(encoding="ascii").strip()
    executable = args.executable.resolve() if args.executable else read_last_browser(profile_root)
    LOGGER.info("Last Version: %s", last_version)
    LOGGER.info("Chromium executable: %s", executable)
    LOGGER.info("Profile directory được chọn: Default")
    LOGGER.info("Exit type trước khi mở: %s", preferences.get("profile", {}).get("exit_type"))

    references = find_original_profile_references(local_state)
    if references:
        LOGGER.warning("Phát hiện %s tham chiếu tuyệt đối về cây GemLogin gốc.", len(references))
        for path, _ in references:
            LOGGER.warning("  Khóa chứa tham chiếu: %s", path)
        if args.rewrite_copy_paths:
            rewrite_original_references_in_copy(profile_root)
            remaining = find_original_profile_references(load_json(profile_root / "Local State"))
            if remaining:
                raise ProbeError(
                    "Vẫn còn tham chiếu về profile gốc sau bước chuẩn bị; từ chối mở browser."
                )
        elif not args.inspect_only:
            raise ProbeError(
                "Từ chối mở vì bản copy còn tham chiếu profile gốc. "
                "Chạy lại với --rewrite-copy-paths để sao lưu và sửa riêng Local State của bản copy."
            )

    ensure_profile_unlocked(profile_root)
    LOGGER.info("Không phát hiện khóa hoặc browser process dùng bản copy.")

    if args.inspect_only:
        LOGGER.info("Inspect-only hoàn tất; chưa khởi chạy Chromium và chưa ghi profile.")
        return 0

    if not executable.is_file():
        raise ProbeError(f"Executable không tồn tại: {executable}")

    LOGGER.warning(
        "Không truyền proxy riêng; lần chạy này dùng cấu hình mạng hệ thống/browser hiện tại."
    )
    LOGGER.info("Khởi chạy Iron/Chromium bằng launch_persistent_context()...")
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_root),
                executable_path=str(executable),
                headless=args.headless,
                no_viewport=True,
                accept_downloads=False,
                timeout=args.launch_timeout_ms,
                ignore_default_args=["--disable-extensions"],
                args=[
                    "--profile-directory=Default",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-session-crashed-bubble",
                ],
            )
            try:
                LOGGER.info("Persistent context đã mở thành công.")
                LOGGER.info("Số page có sẵn sau launch: %s", len(context.pages))
                check_sites(context, args.navigation_timeout_ms, args.settle_ms)
            finally:
                LOGGER.info("Đóng persistent context sạch sẽ...")
                context.close()
    except PlaywrightError as exc:
        explanation = classify_launch_error(exc)
        raise ProbeError(f"{explanation} Chi tiết Playwright: {exc}") from exc

    LOGGER.info("Hoàn tất kiểm tra profile copy.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kiểm tra và mở bản copy profile GemLogin bằng Playwright Python."
    )
    parser.add_argument(
        "--profile-copy",
        type=Path,
        required=True,
        help="Chromium User Data Directory đã copy, ví dụ C:\\profiles\\profiles\\1.",
    )
    parser.add_argument(
        "--executable",
        type=Path,
        help="Chromium executable; mặc định đọc chính xác từ file 'Last Browser'.",
    )
    parser.add_argument(
        "--inspect-only",
        action="store_true",
        help="Chỉ đọc cấu trúc và khóa; không sửa hoặc mở Chromium.",
    )
    parser.add_argument(
        "--rewrite-copy-paths",
        action="store_true",
        help="Sao lưu và thay tham chiếu profile gốc trong Local State của bản copy.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Chạy Chromium ẩn; mặc định mở cửa sổ thật.",
    )
    parser.add_argument("--launch-timeout-ms", type=int, default=60_000)
    parser.add_argument("--navigation-timeout-ms", type=int, default=60_000)
    parser.add_argument("--settle-ms", type=int, default=4_000)
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args()
    configure_logging(args.log_file)
    return args


def main() -> int:
    args = parse_args()
    try:
        return run_probe(args)
    except ProbeError as exc:
        LOGGER.error("Probe thất bại: %s", exc)
        return 2
    except KeyboardInterrupt:
        LOGGER.warning("Người dùng đã dừng probe.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
