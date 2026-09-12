from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
from playwright.sync_api import sync_playwright

from services.browser.local_chromium_config import (
    LocalChromiumConfig,
    LocalChromiumError,
    LocalProxyConfig,
    _executable_major_version,
    classify_local_chromium_error,
    local_chromium_recovery_action,
    normalized_path,
    resolve_local_chromium_config,
    summarize_local_chromium_error,
)
from services.browser.local_chromium_recovery import (
    _browser_processes_using,
    _cross_process_profile_lock,
    _profile_lock,
    ensure_local_profile_unlocked,
    inspect_local_chromium_profile,
    prepare_local_chromium_copy,
    quarantine_stale_profile_locks,
    wait_for_local_profile_unlocked,
)


LOGGER = logging.getLogger(__name__)
_PROFILE_HEALTH: dict[str, dict[str, Any]] = {}
_PROFILE_HEALTH_GUARD = threading.RLock()
_ACTIVE_SESSION_CONDITION = threading.Condition(threading.RLock())
_ACTIVE_SESSIONS: dict[str, dict[str, Any]] = {}


class LocalPersistentBrowser:
    """Small Browser-compatible facade around a persistent context."""

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


class AttachedLocalBrowser:
    """Browser facade for a second Playwright client attached over local CDP."""

    is_local_persistent = True

    def __init__(self, browser):
        self._browser = browser

    @property
    def contexts(self) -> list:
        return list(self._browser.contexts)

    def is_connected(self) -> bool:
        return bool(self._browser.is_connected())


def _register_active_session(config: LocalChromiumConfig) -> None:
    with _ACTIVE_SESSION_CONDITION:
        _ACTIVE_SESSIONS[config.key] = {
            "attachment_count": 0,
            "closing": False,
        }
        _ACTIVE_SESSION_CONDITION.notify_all()


def _reserve_active_session(config: LocalChromiumConfig) -> bool:
    with _ACTIVE_SESSION_CONDITION:
        session = _ACTIVE_SESSIONS.get(config.key)
        if session is None or session.get("closing"):
            return False
        session["attachment_count"] = int(session.get("attachment_count") or 0) + 1
        return True


def _release_active_session(config: LocalChromiumConfig) -> None:
    with _ACTIVE_SESSION_CONDITION:
        session = _ACTIVE_SESSIONS.get(config.key)
        if session is None:
            return
        session["attachment_count"] = max(
            0,
            int(session.get("attachment_count") or 0) - 1,
        )
        _ACTIVE_SESSION_CONDITION.notify_all()


def _wait_for_active_session_attachments(config: LocalChromiumConfig) -> None:
    with _ACTIVE_SESSION_CONDITION:
        session = _ACTIVE_SESSIONS.get(config.key)
        if session is None:
            return
        attachment_count = int(session.get("attachment_count") or 0)
        if attachment_count > 0:
            LOGGER.info(
                "Giữ Chromium mở để chờ %s tác vụ đang dùng chung hoàn tất: %s",
                attachment_count,
                config.key,
            )
        while int(session.get("attachment_count") or 0) > 0:
            _ACTIVE_SESSION_CONDITION.wait(timeout=0.5)
        session["closing"] = True
        _ACTIVE_SESSIONS.pop(config.key, None)
        _ACTIVE_SESSION_CONDITION.notify_all()
        if attachment_count > 0:
            LOGGER.info(
                "Các tác vụ dùng chung Chromium đã hoàn tất; có thể đóng phiên: %s",
                config.key,
            )


def _chrome_user_agent(executable_path: Path) -> str:
    """Use a normal Chrome UA so sites do not receive the HeadlessChrome token."""
    major = _executable_major_version(executable_path)
    if major is None:
        return ""
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


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
            "suggested_action": (
                local_chromium_recovery_action(error) if error else ""
            ),
            **details,
        }


def get_local_chromium_health(
    profile_config: dict[str, Any],
) -> dict[str, Any]:
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


def get_local_chromium_processes(
    profile_config: dict[str, Any],
) -> list[psutil.Process]:
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
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
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
    if options["headless"]:
        user_agent = _chrome_user_agent(config.executable_path)
        if user_agent:
            options["user_agent"] = user_agent
    if config.proxy is not None:
        options["proxy"] = config.proxy.as_playwright_options()
    return options


def _launch_persistent_browser(
    config: LocalChromiumConfig,
    *,
    max_attempts: int,
    delay: float,
    resource_saving: bool,
) -> tuple[object, object, object, LocalPersistentBrowser]:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        manager = sync_playwright()
        playwright = None
        context = None
        safe_mode = attempt > 1
        try:
            if safe_mode:
                inspection = inspect_local_chromium_profile(
                    {
                        "browser": {
                            "user_data_dir": str(config.user_data_dir),
                            "executable_path": str(config.executable_path),
                            "profile_directory": config.profile_directory,
                            "headless": config.headless,
                            "background": config.background,
                            "launch_timeout_ms": config.launch_timeout_ms,
                            "proxy": (
                                {
                                    "enabled": True,
                                    **config.proxy.as_playwright_options(),
                                }
                                if config.proxy
                                else {}
                            ),
                        }
                    },
                    repair_stale_locks=True,
                )
                if not inspection.get("ready"):
                    raise LocalChromiumError(str(inspection.get("message") or ""))
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
            )
            return manager, playwright, context, browser
        except Exception as exc:
            last_error = exc
            _record_health(
                config.key,
                "unhealthy",
                summarize_local_chromium_error(exc),
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
            if attempt < max_attempts and delay:
                time.sleep(delay)
    raise LocalChromiumError(
        f"Không thể mở Chromium profile sau {max_attempts} lần: "
        f"{summarize_local_chromium_error(last_error)}"
    ) from last_error


def _running_browser_cdp_endpoint(config: LocalChromiumConfig) -> str:
    """Read the loopback CDP endpoint exposed by a Dyna-launched Chromium."""
    port_file = config.user_data_dir / "DevToolsActivePort"
    try:
        first_line = port_file.read_text(encoding="utf-8").splitlines()[0].strip()
        port = int(first_line)
    except (OSError, UnicodeError, ValueError, IndexError):
        return ""
    if not 1 <= port <= 65535:
        return ""
    return f"http://127.0.0.1:{port}"


def _attach_to_running_browser(config: LocalChromiumConfig):
    if not _reserve_active_session(config):
        return None
    endpoint = _running_browser_cdp_endpoint(config)
    if not endpoint:
        _release_active_session(config)
        return None
    manager = sync_playwright()
    playwright = None
    try:
        playwright = manager.start()
        browser = playwright.chromium.connect_over_cdp(
            endpoint,
            timeout=config.launch_timeout_ms,
        )
        if not browser.contexts:
            raise LocalChromiumError("Chromium đang chạy nhưng CDP không có context.")
        return manager, playwright, AttachedLocalBrowser(browser)
    except Exception as exc:
        try:
            if playwright is not None:
                playwright.stop()
            else:
                manager.__exit__(None, None, None)
        except Exception:
            pass
        _release_active_session(config)
        LOGGER.warning("Không thể dùng chung Chromium đang chạy qua CDP: %s", exc)
        return None


@contextmanager
def connected_local_chromium_profile(
    profile_config: dict[str, Any],
    *,
    attempts: int = 2,
    retry_delay_seconds: float = 1,
    resource_saving: bool = False,
):
    config = resolve_local_chromium_config(profile_config)
    inspection = inspect_local_chromium_profile(
        profile_config,
        repair_stale_locks=True,
    )
    if not inspection.get("ready"):
        if inspection.get("code") == "profile_in_use":
            attached = _attach_to_running_browser(config)
            if attached is not None:
                _manager, playwright, browser = attached
                _record_health(config.key, "healthy", attached=True)
                try:
                    yield browser
                finally:
                    try:
                        playwright.stop()
                    except Exception:
                        pass
                    _release_active_session(config)
                return
        raise LocalChromiumError(
            f"{inspection.get('message')} {inspection.get('suggested_action')}".strip()
        )
    max_attempts = max(1, int(attempts))
    delay = max(0.0, float(retry_delay_seconds))

    with _profile_lock(config.key), _cross_process_profile_lock(config.key):
        if not wait_for_local_profile_unlocked(config):
            ensure_local_profile_unlocked(config)
        manager, playwright, context, browser = _launch_persistent_browser(
            config,
            max_attempts=max_attempts,
            delay=delay,
            resource_saving=resource_saving,
        )
        _register_active_session(config)
        try:
            yield browser
        finally:
            _wait_for_active_session_attachments(config)
            browser.mark_closed()
            try:
                context.close()
            except Exception:
                pass
            try:
                playwright.stop()
            except Exception:
                try:
                    manager.__exit__(None, None, None)
                except Exception:
                    pass
            if not wait_for_local_profile_unlocked(config, timeout_seconds=10):
                remaining_processes = _browser_processes_using(config.user_data_dir)
                if remaining_processes:
                    LOGGER.warning(
                        "Chromium chưa đóng hoàn toàn; còn %s tiến trình đang dùng Profile %s.",
                        len(remaining_processes),
                        config.key,
                    )
                else:
                    LOGGER.debug(
                        "Chromium đã đóng; file khóa tạm sẽ được dọn ở lần mở tiếp theo: %s",
                        config.key,
                    )


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
        raise LocalChromiumError(
            "Không thể tạo tab automation trong local Chromium."
        ) from exc
