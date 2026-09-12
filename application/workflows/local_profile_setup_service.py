from __future__ import annotations

import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright
from core.runtime_paths import state_dir

from services.browser.browser_runtime_service import (
    install_playwright_chromium_runtime,
    list_browser_runtimes,
    verify_browser_runtime,
)
from services.browser.local_chromium_recovery import (
    _browser_processes_using,
)
from services.browser.local_chromium_browser_service import (
    LocalChromiumConfig,
    classify_local_chromium_error,
    close_local_chromium_profile,
    inspect_local_chromium_profile,
    local_chromium_launch_options,
    local_chromium_recovery_action,
    quarantine_stale_profile_locks,
    resolve_local_chromium_config,
    summarize_local_chromium_error,
)
from services.browser.local_chromium_config import (
    _is_stock_supported_browser,
    installed_supported_browser_candidates,
)
from application.tracking.profile_management_service import ProfileManagementService


LOGIN_URLS = (
    "https://www.facebook.com/",
    "https://www.tiktok.com/",
    "https://studio.youtube.com/",
    "https://www.douyin.com/",
)
ACTIVE_SETUP_STATUSES = {"starting", "running", "closing"}


class LocalProfileSetupError(RuntimeError):
    pass


def default_local_profile_root() -> Path:
    local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local_app_data:
        return Path(local_app_data) / "Dyna" / "browser-profiles"
    return state_dir() / "browser-profiles"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _native_browser_label(executable: Path) -> str:
    return "Microsoft Edge" if executable.name.casefold() == "msedge.exe" else "Google Chrome"


class LocalProfileSetupService:
    """Owns visible first-login sessions for new Dyna-managed Chromium profiles."""

    def __init__(
        self,
        profiles: ProfileManagementService | None = None,
        *,
        profile_root: str | os.PathLike | None = None,
        runtime_root: str | os.PathLike | None = None,
        playwright_factory=sync_playwright,
        native_login: bool | None = None,
        native_launcher=subprocess.Popen,
    ):
        self.profiles = profiles or ProfileManagementService()
        self.profile_root = Path(profile_root or default_local_profile_root())
        self.runtime_root = Path(runtime_root) if runtime_root else None
        self._playwright_factory = playwright_factory
        self._native_login = (
            playwright_factory is sync_playwright
            if native_login is None
            else bool(native_login)
        )
        self._native_launcher = native_launcher
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _public_session(session: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in session.items()
            if not key.startswith("_") and key not in {"thread"}
        }

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                profile_id: self._public_session(session)
                for profile_id, session in self._sessions.items()
            }

    def get(self, profile_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._sessions.get(str(profile_id))
            return self._public_session(session) if session else None

    def check(
        self,
        profile_id: str,
        *,
        repair_stale_locks: bool = True,
    ) -> dict[str, Any]:
        profile_id = self.profiles.normalize_profile_id(profile_id)
        profile = self.profiles.load(profile_id)
        result = inspect_local_chromium_profile(
            profile,
            repair_stale_locks=repair_stale_locks,
        )
        if result.get("ready"):
            try:
                config = resolve_local_chromium_config(profile)
            except Exception:
                return {"profile_id": profile_id, **result}
            current = str((profile.get("browser") or {}).get("executable_path") or "")
            if os.path.normcase(os.path.abspath(current)) != os.path.normcase(
                str(config.executable_path)
            ):
                browser = dict(profile.get("browser") or {})
                browser["executable_path"] = str(config.executable_path)
                profile["browser"] = browser
                self.profiles.save(profile_id, profile)
        return {"profile_id": profile_id, **result}

    def _set(self, profile_id: str, **changes: Any) -> None:
        with self._lock:
            session = self._sessions.get(profile_id)
            if session is None:
                return
            session.update(changes)
            session["updated_at"] = _now()
            session["active"] = str(session.get("status")) in ACTIVE_SETUP_STATUSES

    def _select_executable(self, executable_path: str) -> Path:
        configured = str(executable_path or "").strip()
        configured_executable = Path(configured) if configured else None
        if (
            configured_executable is not None
            and configured_executable.is_file()
            and (not self._native_login or _is_stock_supported_browser(configured_executable))
        ):
            if (configured_executable.parent / "dyna-runtime.json").is_file():
                verify_browser_runtime(configured_executable, verify_hashes=False)
            return configured_executable.resolve()

        if self._native_login:
            supported = installed_supported_browser_candidates()
            if supported:
                return supported[0]

        if configured_executable is not None and configured_executable.is_file():
            if (configured_executable.parent / "dyna-runtime.json").is_file():
                verify_browser_runtime(configured_executable, verify_hashes=False)
            return configured_executable.resolve()

        if configured:
            executable = configured_executable
            assert executable is not None
            if not executable.is_absolute() or not executable.is_file():
                raise LocalProfileSetupError(
                    f"Chromium executable không tồn tại: {configured}"
                )
            if (executable.parent / "dyna-runtime.json").is_file():
                verify_browser_runtime(executable, verify_hashes=False)
            return executable.resolve()

        runtimes = [row for row in list_browser_runtimes(self.runtime_root) if row.get("valid")]
        runtimes.sort(
            key=lambda row: (
                str(row.get("runtime_id") or "") != "chromium-1223",
                not str(row.get("runtime_id") or "").startswith("chromium-"),
                str(row.get("runtime_id") or ""),
            )
        )
        if runtimes:
            return Path(str(runtimes[0]["executable_path"])).resolve()
        runtime = install_playwright_chromium_runtime(destination_root=self.runtime_root)
        return Path(runtime.executable_path).resolve()

    def start(self, profile_id: str, *, executable_path: str = "") -> dict[str, Any]:
        profile_id = self.profiles.normalize_profile_id(profile_id)
        profile = self.profiles.load(profile_id)
        current_browser = dict(profile.get("browser") or {})
        current_user_data_value = str(current_browser.get("user_data_dir") or "").strip()
        if (
            str(current_browser.get("provider") or "") == "local_chromium"
            and current_user_data_value
            and Path(current_user_data_value).is_dir()
        ):
            raise LocalProfileSetupError(
                "Profile đã có Local Chromium. Hãy dùng chức năng mở đăng nhập lại thay vì tạo mới."
            )

        target = (self.profile_root / f"profile-{profile_id}").resolve()
        if target.exists() and any(target.iterdir()):
            target = (
                self.profile_root
                / f"profile-{profile_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            ).resolve()
        executable = self._select_executable(executable_path)
        return self._start_session(
            profile_id,
            target,
            executable,
            mode="create",
            profile_directory="Default",
            launch_timeout_ms=60_000,
        )

    def open_existing(self, profile_id: str) -> dict[str, Any]:
        profile_id = self.profiles.normalize_profile_id(profile_id)
        profile = self.profiles.load(profile_id)
        try:
            inspection = self.check(profile_id, repair_stale_locks=True)
            if not inspection.get("ready"):
                raise LocalProfileSetupError(
                    f"{inspection.get('message')} {inspection.get('suggested_action')}".strip()
                )
            config = resolve_local_chromium_config(profile)
        except Exception as exc:
            raise LocalProfileSetupError(str(exc)) from exc
        executable = (
            self._select_executable(str(config.executable_path))
            if self._native_login
            else config.executable_path
        )
        return self._start_session(
            profile_id,
            config.user_data_dir,
            executable,
            mode="existing",
            profile_directory=config.profile_directory,
            launch_timeout_ms=config.launch_timeout_ms,
        )

    def _start_session(
        self,
        profile_id: str,
        target: Path,
        executable: Path,
        *,
        mode: str,
        profile_directory: str,
        launch_timeout_ms: int,
    ) -> dict[str, Any]:

        with self._lock:
            current = self._sessions.get(profile_id) or {}
            if str(current.get("status") or "") in ACTIVE_SETUP_STATUSES:
                raise LocalProfileSetupError("Profile đang có một cửa sổ thiết lập đăng nhập.")
            stop_event = threading.Event()
            now = _now()
            session: dict[str, Any] = {
                "profile_id": profile_id,
                "status": "starting",
                "active": True,
                "message": (
                    "Đang mở trình duyệt chính thức để đăng nhập."
                    if mode == "existing"
                    else "Đang tạo hồ sơ và mở trình duyệt chính thức để đăng nhập."
                ),
                "user_data_dir": str(target),
                "executable_path": str(executable),
                "profile_directory": profile_directory,
                "launch_timeout_ms": max(5_000, int(launch_timeout_ms)),
                "mode": mode,
                "login_mode": "native" if self._native_login else "playwright",
                "fingerprint_mode": "system",
                "browser_name": (
                    _native_browser_label(executable)
                    if self._native_login
                    else "Chromium"
                ),
                "sessions": {},
                "last_error": "",
                "error_code": "",
                "suggested_action": "",
                "recovery_mode": "",
                "started_at": now,
                "updated_at": now,
                "_stop_event": stop_event,
            }
            thread = threading.Thread(
                target=self._run_session,
                args=(profile_id,),
                daemon=True,
                name=f"local-profile-setup-{profile_id}",
            )
            session["thread"] = thread
            self._sessions[profile_id] = session
            thread.start()
            return self._public_session(session)

    def finish(self, profile_id: str) -> dict[str, Any]:
        profile_id = self.profiles.normalize_profile_id(profile_id)
        with self._lock:
            session = self._sessions.get(profile_id)
            if not session:
                raise LocalProfileSetupError("Profile chưa có phiên thiết lập Local Chromium.")
            if str(session.get("status") or "") not in ACTIVE_SETUP_STATUSES:
                return self._public_session(session)
            session["status"] = "closing"
            session["message"] = "Đang lưu session và đóng cửa sổ đăng nhập."
            session["updated_at"] = _now()
            session["_stop_event"].set()
            return self._public_session(session)

    def shutdown(self, timeout_seconds: float = 10) -> dict[str, dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values())
            for session in sessions:
                if str(session.get("status") or "") in ACTIVE_SETUP_STATUSES:
                    session["status"] = "closing"
                    session["message"] = "Dyna đang đóng cửa sổ thiết lập Local Profile."
                    session["_stop_event"].set()
            threads = [session.get("thread") for session in sessions]
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        for thread in threads:
            if not isinstance(thread, threading.Thread) or thread is threading.current_thread():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        return self.snapshot()

    @staticmethod
    def _session_markers(context) -> dict[str, bool]:
        try:
            cookies = context.cookies()
        except Exception:
            return {}
        by_domain: dict[str, set[str]] = {}
        for cookie in cookies:
            domain = str(cookie.get("domain") or "").lstrip(".").casefold()
            by_domain.setdefault(domain, set()).add(str(cookie.get("name") or ""))

        def names_for(domain_suffix: str) -> set[str]:
            names: set[str] = set()
            for domain, values in by_domain.items():
                if domain == domain_suffix or domain.endswith("." + domain_suffix):
                    names.update(values)
            return names

        facebook = names_for("facebook.com")
        tiktok = names_for("tiktok.com")
        douyin = names_for("douyin.com")
        google = names_for("google.com") | names_for("youtube.com")
        return {
            "facebook": "c_user" in facebook,
            "tiktok": bool({"sessionid", "sessionid_ss", "sid_tt"} & tiktok),
            "douyin": bool({"sessionid", "sessionid_ss", "sid_guard"} & douyin),
            "youtube": bool({"SID", "HSID", "SAPISID", "__Secure-1PSID"} & google),
        }

    @staticmethod
    def _open_login_pages(context) -> None:
        pages = list(context.pages)
        for index, url in enumerate(LOGIN_URLS):
            try:
                page = pages[0] if index == 0 and pages else context.new_page()
                page.goto(url, wait_until="commit", timeout=15_000)
            except Exception:
                continue

    def _persist_profile_config(self, profile_id: str, session: dict[str, Any]) -> None:
        profile = self.profiles.load(profile_id)
        browser = dict(profile.get("browser") or {})
        browser.update(
            {
                "provider": "local_chromium",
                "user_data_dir": str(session["user_data_dir"]),
                "executable_path": str(session["executable_path"]),
                "profile_directory": str(session.get("profile_directory") or "Default"),
                "login_mode": str(session.get("login_mode") or "native"),
                "fingerprint_mode": "system",
            }
        )
        browser.setdefault("headless", False)
        browser.setdefault("background", True)
        browser.setdefault("launch_timeout_ms", 60_000)
        profile["browser"] = browser
        resolve_local_chromium_config(profile)
        self.profiles.save(profile_id, profile)

    @staticmethod
    def _native_profile_ready(user_data_dir: Path, profile_directory: str) -> bool:
        return all(
            path.exists()
            for path in (
                user_data_dir / "Local State",
                user_data_dir / profile_directory,
                user_data_dir / profile_directory / "Preferences",
            )
        )

    @staticmethod
    def _native_launch_command(
        executable: Path,
        user_data_dir: Path,
        profile_directory: str,
    ) -> list[str]:
        return [
            str(executable),
            f"--user-data-dir={user_data_dir}",
            f"--profile-directory={profile_directory}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--new-window",
            *LOGIN_URLS,
        ]

    def _close_native_browser(
        self,
        process: Any,
        user_data_dir: Path,
        executable_path: Path,
        profile_directory: str,
    ) -> None:
        if self._native_profile_ready(user_data_dir, profile_directory):
            try:
                close_local_chromium_profile(
                    {
                        "browser": {
                            "provider": "local_chromium",
                            "user_data_dir": str(user_data_dir),
                            "executable_path": str(executable_path),
                            "profile_directory": profile_directory,
                        }
                    }
                )
            except Exception:
                pass
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _run_native_session(self, profile_id: str) -> None:
        with self._lock:
            session = self._sessions[profile_id]
            stop_event: threading.Event = session["_stop_event"]
            user_data_dir = Path(str(session["user_data_dir"]))
            executable_path = Path(str(session["executable_path"]))
            profile_directory = str(session["profile_directory"])
            launch_timeout_ms = int(session.get("launch_timeout_ms") or 60_000)

        process = None
        try:
            user_data_dir.mkdir(parents=True, exist_ok=True)
            command = self._native_launch_command(
                executable_path,
                user_data_dir,
                profile_directory,
            )
            process = self._native_launcher(
                command,
                cwd=str(executable_path.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._set(
                profile_id,
                status="running",
                message=(
                    f"{_native_browser_label(executable_path)} đang mở ở chế độ bình thường. "
                    "Fingerprint dùng thông số thật, đồng nhất của máy. "
                    "Hãy đăng nhập rồi bấm Đã đăng nhập xong trong Dyna."
                ),
                last_error="",
                error_code="",
                suggested_action="",
            )

            while not stop_event.wait(0.25):
                if process.poll() is None:
                    continue
                try:
                    active_processes = _browser_processes_using(user_data_dir)
                except Exception:
                    active_processes = []
                if active_processes:
                    continue
                break

            if stop_event.is_set():
                self._close_native_browser(
                    process,
                    user_data_dir,
                    executable_path,
                    profile_directory,
                )
            process = None
            if not self._native_profile_ready(user_data_dir, profile_directory):
                raise LocalProfileSetupError(
                    "Trình duyệt chưa tạo xong dữ liệu hồ sơ. Hãy mở lại và chờ trang đăng nhập xuất hiện."
                )
            self._persist_profile_config(profile_id, session)
            self._set(
                profile_id,
                status="completed",
                message=(
                    f"Đã lưu hồ sơ {_native_browser_label(executable_path)} riêng. "
                    "Dyna dùng fingerprint thật, đồng nhất của máy."
                ),
                sessions={},
                completed_at=_now(),
                last_error="",
                error_code="",
                suggested_action="",
            )
        except Exception as exc:
            self._set(
                profile_id,
                status="error",
                message="Không thể hoàn tất hồ sơ trình duyệt chính thức.",
                last_error=summarize_local_chromium_error(exc),
                error_code=classify_local_chromium_error(exc),
                suggested_action=local_chromium_recovery_action(exc),
            )
        finally:
            if process is not None:
                self._close_native_browser(
                    process,
                    user_data_dir,
                    executable_path,
                    profile_directory,
                )

    def _run_session(self, profile_id: str) -> None:
        if self._native_login:
            self._run_native_session(profile_id)
            return
        self._run_playwright_session(profile_id)

    def _run_playwright_session(self, profile_id: str) -> None:
        with self._lock:
            session = self._sessions[profile_id]
            stop_event: threading.Event = session["_stop_event"]
            user_data_dir = str(session["user_data_dir"])
            executable_path = str(session["executable_path"])
            profile_directory = str(session["profile_directory"])
            launch_timeout_ms = int(session.get("launch_timeout_ms") or 60_000)
            mode = str(session.get("mode") or "create")

        manager = None
        playwright = None
        context = None
        closed_event = threading.Event()
        try:
            Path(user_data_dir).mkdir(parents=True, exist_ok=True)
            launch_config = LocalChromiumConfig(
                user_data_dir=Path(user_data_dir),
                executable_path=Path(executable_path),
                profile_directory=profile_directory,
                headless=False,
                background=False,
                launch_timeout_ms=launch_timeout_ms,
            )
            last_launch_error: Exception | None = None
            opened_safe_mode = False
            for attempt in (1, 2):
                safe_mode = attempt == 2
                manager = self._playwright_factory()
                try:
                    playwright = manager.start()
                    context = playwright.chromium.launch_persistent_context(
                        **local_chromium_launch_options(
                            launch_config,
                            safe_mode=safe_mode,
                            visible=True,
                        )
                    )
                    opened_safe_mode = safe_mode
                    if safe_mode:
                        self._set(
                            profile_id,
                            recovery_mode="safe_mode",
                            message=(
                                "Chromium đã được phục hồi ở chế độ an toàn. "
                                "Hãy kiểm tra đăng nhập rồi bấm Hoàn tất."
                            ),
                        )
                    break
                except Exception as exc:
                    last_launch_error = exc
                    try:
                        if playwright is not None:
                            playwright.stop()
                        elif manager is not None:
                            manager.__exit__(None, None, None)
                    except Exception:
                        pass
                    manager = None
                    playwright = None
                    context = None
                    if attempt == 1:
                        self._set(
                            profile_id,
                            status="starting",
                            message="Lần mở đầu thất bại; Dyna đang thử chế độ an toàn.",
                            last_error=summarize_local_chromium_error(exc),
                            error_code=classify_local_chromium_error(exc),
                            suggested_action=local_chromium_recovery_action(exc),
                        )
                        if mode == "existing":
                            profile = self.profiles.load(profile_id)
                            inspect_local_chromium_profile(
                                profile,
                                repair_stale_locks=True,
                            )
                        else:
                            try:
                                quarantine_stale_profile_locks(launch_config)
                            except Exception:
                                pass
                        continue
                    raise
            if context is None:
                raise LocalProfileSetupError(
                    summarize_local_chromium_error(last_launch_error)
                ) from last_launch_error
            context.on("close", lambda *_: closed_event.set())
            self._set(
                profile_id,
                status="running",
                message=(
                    "Chromium đang chạy ở chế độ an toàn. Hãy kiểm tra đăng nhập, "
                    "sau đó bấm Hoàn tất trong Dyna."
                    if opened_safe_mode
                    else "Hãy đăng nhập các nền tảng trong cửa sổ Chromium, "
                    "sau đó bấm Hoàn tất đăng nhập trong Dyna."
                ),
                last_error="",
                error_code="",
                suggested_action="",
            )
            self._open_login_pages(context)
            while not stop_event.is_set() and not closed_event.wait(0.25):
                pass
            markers = self._session_markers(context) if stop_event.is_set() else {}
            if not closed_event.is_set():
                context.close()
            context = None
            self._persist_profile_config(profile_id, session)
            self._set(
                profile_id,
                status="completed",
                message="Đã tạo Local Chromium và lưu cấu hình Profile.",
                sessions=markers,
                completed_at=_now(),
                last_error="",
                error_code="",
                suggested_action="",
            )
        except Exception as exc:
            error_summary = summarize_local_chromium_error(exc)
            self._set(
                profile_id,
                status="error",
                message="Không thể hoàn tất Local Chromium.",
                last_error=error_summary,
                error_code=classify_local_chromium_error(exc),
                suggested_action=local_chromium_recovery_action(exc),
            )
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            try:
                if playwright is not None:
                    playwright.stop()
                elif manager is not None:
                    manager.__exit__(None, None, None)
            except Exception:
                pass
