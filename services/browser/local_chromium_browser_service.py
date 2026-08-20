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

    with _profile_lock(config.key), _cross_process_profile_lock(config.key):
        if not wait_for_local_profile_unlocked(config):
            ensure_local_profile_unlocked(config)
        manager, playwright, context, browser = _launch_persistent_browser(
            config,
            max_attempts=max_attempts,
            delay=delay,
            resource_saving=resource_saving,
        )
        try:
            yield browser
        finally:
            browser.mark_closed()
            try:
                context.close()
            except Exception:
                pass
            if not wait_for_local_profile_unlocked(config, timeout_seconds=10):
                LOGGER.warning("Chromium vẫn còn khóa sau khi đóng: %s", config.key)
            try:
                playwright.stop()
            except Exception:
                try:
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
        raise LocalChromiumError(
            "Không thể tạo tab automation trong local Chromium."
        ) from exc
