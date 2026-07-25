from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from functools import wraps
from typing import Any

from core.errors import BrowserUnavailableError, ConfigurationError, is_retryable_error
from services.browser.gemlogin_browser_service import (
    classify_automation_error,
    close_gemlogin_profile,
    connected_gemlogin_profile,
    create_background_page as create_gemlogin_background_page,
    get_gemlogin_profile_health,
    is_gemlogin_connection_error,
    run_with_gemlogin_recovery,
)
from services.browser.local_chromium_browser_service import (
    LocalChromiumError,
    close_local_chromium_profile,
    connected_local_chromium_profile,
    create_local_background_page,
    get_local_chromium_health,
)


LOGGER = logging.getLogger(__name__)
SUPPORTED_BROWSER_PROVIDERS = {"gemlogin", "local_chromium"}
_REUSE_STATE = threading.local()


def _reuse_state() -> dict:
    state = getattr(_REUSE_STATE, "value", None)
    if state is None:
        state = {"depth": 0, "connections": {}}
        _REUSE_STATE.value = state
    return state


@contextmanager
def browser_reuse_scope():
    """Reuse Local Chromium within one worker batch, then close it deterministically."""
    state = _reuse_state()
    state["depth"] += 1
    try:
        yield
    finally:
        state["depth"] -= 1
        if state["depth"] == 0:
            connections = list(state["connections"].values())
            state["connections"].clear()
            for manager, _browser in reversed(connections):
                try:
                    manager.__exit__(None, None, None)
                except Exception:
                    LOGGER.exception("Không thể đóng Local Chromium reuse scope sạch sẽ.")


def reuse_browser_connections(function):
    """Decorator for worker operations that may open the same browser repeatedly."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with browser_reuse_scope():
            return function(*args, **kwargs)
    return wrapped


def browser_provider(profile_config: dict[str, Any] | None) -> str:
    browser = dict((profile_config or {}).get("browser") or {})
    provider = str(browser.get("provider") or "local_chromium").strip().casefold()
    if provider not in SUPPORTED_BROWSER_PROVIDERS:
        raise ConfigurationError(f"Nhà cung cấp trình duyệt không được hỗ trợ: {provider}")
    return provider


def browser_profile_label(
    browser_profile_id: str,
    profile_config: dict[str, Any] | None = None,
) -> str:
    if browser_provider(profile_config) == "local_chromium":
        browser = dict((profile_config or {}).get("browser") or {})
        return str(browser.get("user_data_dir") or "Local Chromium")
    return f"GemLogin {str(browser_profile_id or '').strip()}"


@contextmanager
def connected_browser_profile(
    browser_profile_id: str,
    api_url: str,
    *,
    profile_config: dict[str, Any] | None = None,
    attempts: int = 3,
    retry_delay_seconds: float = 2,
    cdp_ready_timeout_seconds: float = 20,
    resource_saving: bool = False,
    close_profile_on_exit: bool = False,
):
    provider = browser_provider(profile_config)
    if provider == "local_chromium":
        state = _reuse_state()
        if state["depth"] <= 0:
            with connected_local_chromium_profile(
                profile_config or {},
                attempts=attempts,
                retry_delay_seconds=retry_delay_seconds,
                resource_saving=resource_saving,
            ) as browser:
                yield browser
            return

        connection_key = (
            browser_profile_label(browser_profile_id, profile_config),
            bool(resource_saving),
        )
        connection = state["connections"].get(connection_key)
        if connection is None:
            manager = connected_local_chromium_profile(
                profile_config or {},
                attempts=attempts,
                retry_delay_seconds=retry_delay_seconds,
                resource_saving=resource_saving,
            )
            browser = manager.__enter__()
            connection = (manager, browser)
            state["connections"][connection_key] = connection
        manager, browser = connection
        try:
            yield browser
        except Exception as exc:
            if is_browser_connection_error(exc, profile_config):
                state["connections"].pop(connection_key, None)
                manager.__exit__(type(exc), exc, exc.__traceback__)
            raise
        return

    with connected_gemlogin_profile(
        browser_profile_id,
        api_url,
        attempts=attempts,
        retry_delay_seconds=retry_delay_seconds,
        cdp_ready_timeout_seconds=cdp_ready_timeout_seconds,
        close_profile_on_exit=close_profile_on_exit,
    ) as browser:
        yield browser


def create_background_page(browser, context, *, timeout_ms: int = 15_000):
    if bool(getattr(browser, "is_local_persistent", False)):
        return create_local_background_page(context)
    return create_gemlogin_background_page(browser, context, timeout_ms=timeout_ms)


def configure_lightweight_scan_page(page) -> bool:
    """Block heavyweight rendering resources while keeping API/XHR traffic intact."""
    blocked_types = {"image", "media", "font"}

    def handle_route(route, request) -> None:
        try:
            resource_type = str(getattr(request, "resource_type", "") or "").casefold()
            if resource_type in blocked_types:
                route.abort()
            else:
                route.continue_()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    configured = False
    try:
        page.route("**/*", handle_route)
        configured = True
    except Exception as exc:
        LOGGER.debug("Không thể chặn image/media/font cho tab quét: %s", exc)
    try:
        page.emulate_media(reduced_motion="reduce")
    except Exception:
        pass
    try:
        page.add_init_script(
            """
            (() => {
              const disableMotion = () => {
                if (document.getElementById('__dyna_no_motion')) return;
                const style = document.createElement('style');
                style.id = '__dyna_no_motion';
                style.textContent = '*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}';
                (document.head || document.documentElement).appendChild(style);
              };
              document.addEventListener('DOMContentLoaded', disableMotion, {once: true});
              if (document.documentElement) disableMotion();
            })();
            """
        )
    except Exception:
        pass
    return configured


def is_browser_connection_error(
    error: object,
    profile_config: dict[str, Any] | None = None,
) -> bool:
    if isinstance(error, BrowserUnavailableError):
        return True
    if browser_provider(profile_config) == "gemlogin":
        return is_gemlogin_connection_error(error)
    if isinstance(error, LocalChromiumError):
        return True
    return classify_automation_error(error) == "cdp"


def run_with_browser_recovery(
    operation,
    browser_profile_id: str,
    api_url: str,
    *,
    profile_config: dict[str, Any] | None = None,
    operation_name: str = "automation",
    attempts: int = 2,
    retry_delay_seconds: float = 2,
):
    if browser_provider(profile_config) == "gemlogin":
        return run_with_gemlogin_recovery(
            operation,
            browser_profile_id,
            api_url,
            operation_name=operation_name,
            attempts=attempts,
            retry_delay_seconds=retry_delay_seconds,
        )

    max_attempts = max(1, int(attempts))
    delay = max(0.0, float(retry_delay_seconds))
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            typed_error = exc if isinstance(exc, BrowserUnavailableError) else (
                BrowserUnavailableError(str(exc), cause=exc)
                if is_browser_connection_error(exc, profile_config)
                else exc
            )
            last_error = typed_error
            if not is_retryable_error(typed_error) or attempt >= max_attempts:
                if typed_error is exc:
                    raise
                raise typed_error from exc
            LOGGER.warning(
                "%s mất kết nối Local Chromium ở lần %s/%s; sẽ thử lại: %s",
                operation_name,
                attempt,
                max_attempts,
                exc,
            )
            if delay:
                time.sleep(delay)
    raise BrowserUnavailableError(
        f"{operation_name} thất bại: {last_error}", cause=last_error
    ) from last_error


def close_browser_profile(
    browser_profile_id: str,
    api_url: str,
    *,
    profile_config: dict[str, Any] | None = None,
    timeout: int = 30,
) -> None:
    if browser_provider(profile_config) == "local_chromium":
        close_local_chromium_profile(profile_config or {})
        return
    close_gemlogin_profile(browser_profile_id, api_url, timeout=timeout)


def get_browser_profile_health(
    browser_profile_id: str,
    profile_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if browser_provider(profile_config) == "local_chromium":
        return get_local_chromium_health(profile_config or {})
    health = get_gemlogin_profile_health(browser_profile_id)
    health.setdefault("provider", "gemlogin")
    return health
