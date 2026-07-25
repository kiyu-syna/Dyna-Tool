from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright

from core.errors import BrowserUnavailableError

_CACHE_LOCK = threading.RLock()
_DEBUG_ADDRESS_CACHE: dict[str, str] = {}
_PROFILE_HEALTH: dict[str, dict] = {}
_HEALTH_LOCK = threading.RLock()
_PLAYWRIGHT_OPERATION_LOCK = threading.RLock()
_PLAYWRIGHT_PATCH_LOCK = threading.Lock()
_PLAYWRIGHT_PATCH_CHECKED = False
_LOGGER = logging.getLogger(__name__)
_WINDOWS_MUTEX_NAME = "Local\\DynaTool.GemLogin.Playwright"
DEFAULT_CDP_READY_TIMEOUT_SECONDS = 20

_SHARED_WORKER_PATCH_MARKER = "DynaTool: detach unsupported shared workers before context validation."
_SHARED_WORKER_VULNERABLE_CODE = """        const session2 = this._session.createChildSession(sessionId);
        assert(targetInfo.browserContextId, \"targetInfo: \" + JSON.stringify(targetInfo, null, 2));"""
_SHARED_WORKER_FIXED_CODE = f"""        const session2 = this._session.createChildSession(sessionId);
        // {_SHARED_WORKER_PATCH_MARKER}
        if (targetInfo.type === \"shared_worker\") {{
          session2.detach().catch(() => {{
          }});
          return;
        }}
        assert(targetInfo.browserContextId, \"targetInfo: \" + JSON.stringify(targetInfo, null, 2));"""

_CDP_ERROR_MARKERS = (
    "connect_over_cdp",
    "econnrefused",
    "connection closed while reading from the driver",
    "browser has been closed",
    "browser closed",
    "target page, context or browser has been closed",
    "target closed",
    "websocket is not open",
    "websocket error",
    "retrieving websocket url",
    "cdp ",
)
_NETWORK_ERROR_MARKERS = (
    "connection reset",
    "connection aborted",
    "connection timed out",
    "read timed out",
    "remote end closed connection",
    "temporary failure",
    "name resolution",
    "net::err_",
)


def classify_automation_error(error: object) -> str:
    """Classify failures so callers only retry operations that are safe to repeat."""
    message = str(error or "").lower()
    if any(marker in message for marker in _CDP_ERROR_MARKERS):
        return "cdp"
    if any(marker in message for marker in _NETWORK_ERROR_MARKERS):
        return "network"
    if "timeout" in message or "timed out" in message:
        return "timeout"
    return "permanent"


def is_gemlogin_connection_error(error: object) -> bool:
    return classify_automation_error(error) == "cdp"


def _record_profile_health(
    gemlogin_profile_id: str,
    status: str,
    *,
    debug_address: str = "",
    error: object = "",
    recovered: bool = False,
) -> None:
    profile_id = str(gemlogin_profile_id or "").strip()
    if not profile_id:
        return
    with _HEALTH_LOCK:
        previous = _PROFILE_HEALTH.get(profile_id, {})
        recovery_count = int(previous.get("recovery_count") or 0)
        if recovered:
            recovery_count += 1
        _PROFILE_HEALTH[profile_id] = {
            "profile_id": profile_id,
            "status": str(status),
            "debug_address": str(debug_address or previous.get("debug_address") or ""),
            "error_category": classify_automation_error(error) if error else "",
            "last_error": str(error or ""),
            "recovery_count": recovery_count,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }


def get_gemlogin_profile_health(gemlogin_profile_id: str) -> dict:
    profile_id = str(gemlogin_profile_id or "").strip()
    with _HEALTH_LOCK:
        return dict(
            _PROFILE_HEALTH.get(
                profile_id,
                {
                    "profile_id": profile_id,
                    "status": "unknown",
                    "debug_address": "",
                    "error_category": "",
                    "last_error": "",
                    "recovery_count": 0,
                    "checked_at": "",
                },
            )
        )


@contextmanager
def _cross_process_playwright_lock():
    if os.name != "nt":
        yield
        return

    from ctypes import wintypes

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

    handle = create_mutex(None, False, _WINDOWS_MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    acquired = False
    try:
        wait_result = wait_for_single_object(handle, 0xFFFFFFFF)
        if wait_result not in (0x00000000, 0x00000080):
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = True
        yield
    finally:
        if acquired:
            release_mutex(handle)
        close_handle(handle)


def _stop_playwright_driver(manager, playwright=None) -> None:
    try:
        if playwright is not None:
            playwright.stop()
        else:
            manager.__exit__(None, None, None)
    except Exception:
        pass


def _playwright_core_bundle_path() -> Path:
    import playwright

    return (
        Path(playwright.__file__).resolve().parent
        / "driver"
        / "package"
        / "lib"
        / "coreBundle.js"
    )


def _patch_playwright_shared_worker_crash(bundle_path: Path) -> bool:
    """Patch a Playwright CDP assertion that crashes on Facebook shared workers."""
    source = bundle_path.read_text(encoding="utf-8")
    if _SHARED_WORKER_PATCH_MARKER in source:
        return False
    if _SHARED_WORKER_VULNERABLE_CODE not in source:
        return False

    patched_source = source.replace(
        _SHARED_WORKER_VULNERABLE_CODE,
        _SHARED_WORKER_FIXED_CODE,
        1,
    )
    temporary_path = bundle_path.with_suffix(f"{bundle_path.suffix}.dynatool.tmp")
    temporary_path.write_text(patched_source, encoding="utf-8")
    os.replace(temporary_path, bundle_path)
    return True


def _ensure_playwright_shared_worker_compatibility() -> None:
    global _PLAYWRIGHT_PATCH_CHECKED
    if _PLAYWRIGHT_PATCH_CHECKED:
        return
    with _PLAYWRIGHT_PATCH_LOCK:
        if _PLAYWRIGHT_PATCH_CHECKED:
            return
        try:
            bundle_path = _playwright_core_bundle_path()
            if _patch_playwright_shared_worker_crash(bundle_path):
                _LOGGER.info(
                    "Đã áp dụng bản vá Playwright cho shared_worker Facebook: %s",
                    bundle_path,
                )
        except Exception as exc:
            _LOGGER.warning(
                "Không thể kiểm tra bản vá Playwright shared_worker; sẽ thử kết nối bình thường: %s",
                exc,
            )
        finally:
            _PLAYWRIGHT_PATCH_CHECKED = True


def _http_debug_url(debug_address: str) -> str:
    value = str(debug_address or "").strip().rstrip("/")
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith(("ws://", "wss://")):
        parsed = urlparse(value)
        return f"http://{parsed.netloc}"
    return f"http://{value}"


def cdp_endpoint(debug_address: str) -> str:
    endpoint = _http_debug_url(debug_address)
    if not endpoint:
        raise RuntimeError("Địa chỉ điều khiển GemLogin bị trống.")
    return endpoint


def _is_cdp_reachable(debug_address: str) -> bool:
    try:
        response = requests.get(
            f"{cdp_endpoint(debug_address)}/json/version",
            timeout=2,
        )
        return response.ok
    except Exception:
        return False


def _wait_for_cdp(
    debug_address: str,
    *,
    timeout_seconds: float = DEFAULT_CDP_READY_TIMEOUT_SECONDS,
    poll_interval_seconds: float = 0.5,
) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        if _is_cdp_reachable(debug_address):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.05, float(poll_interval_seconds)))


def _start_gemlogin_profile(
    gemlogin_profile_id: str,
    api_url: str,
    *,
    timeout: int = 30,
) -> str:
    profile_id = str(gemlogin_profile_id or "").strip()
    response = requests.get(
        f"{str(api_url).rstrip('/')}/api/profiles/start/{profile_id}",
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(str(payload.get("message") or "GemLogin từ chối mở profile."))
    debug_address = str(
        (payload.get("data") or {}).get("remote_debugging_address") or ""
    ).strip()
    if not debug_address:
        raise RuntimeError("GemLogin không trả về địa chỉ điều khiển trình duyệt.")

    with _CACHE_LOCK:
        _DEBUG_ADDRESS_CACHE[profile_id] = debug_address
    return debug_address


def get_gemlogin_debug_address(
    gemlogin_profile_id: str,
    api_url: str,
    *,
    timeout: int = 30,
) -> str:
    """Return a live CDP address and avoid restarting an already-running profile."""
    profile_id = str(gemlogin_profile_id or "").strip()
    if not profile_id:
        raise RuntimeError("Chưa cấu hình GemLogin Profile ID.")

    with _CACHE_LOCK:
        cached = _DEBUG_ADDRESS_CACHE.get(profile_id, "")
    if cached and _is_cdp_reachable(cached):
        return cached
    clear_gemlogin_debug_address(profile_id)
    return _start_gemlogin_profile(profile_id, api_url, timeout=timeout)


def clear_gemlogin_debug_address(gemlogin_profile_id: str) -> None:
    with _CACHE_LOCK:
        _DEBUG_ADDRESS_CACHE.pop(str(gemlogin_profile_id or "").strip(), None)


def get_cached_gemlogin_debug_address(gemlogin_profile_id: str) -> str:
    profile_id = str(gemlogin_profile_id or "").strip()
    with _CACHE_LOCK:
        return str(_DEBUG_ADDRESS_CACHE.get(profile_id, "") or "")


def close_gemlogin_profile(
    gemlogin_profile_id: str,
    api_url: str,
    *,
    timeout: int = 30,
) -> None:
    profile_id = str(gemlogin_profile_id or "").strip()
    if not profile_id:
        raise RuntimeError("Chưa cấu hình GemLogin Profile ID để đóng.")
    try:
        response = requests.get(
            f"{str(api_url).rstrip('/')}/api/profiles/close/{profile_id}",
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("success") is False:
            raise RuntimeError(str(payload.get("message") or "GemLogin từ chối đóng profile."))
    finally:
        clear_gemlogin_debug_address(profile_id)


def restart_gemlogin_profile(
    gemlogin_profile_id: str,
    api_url: str,
    *,
    timeout: int = 30,
    restart_delay_seconds: float = 2,
) -> str:
    """Force a stale GemLogin profile to obtain a new browser process and CDP endpoint."""
    profile_id = str(gemlogin_profile_id or "").strip()
    if not profile_id:
        raise RuntimeError("Chưa cấu hình GemLogin Profile ID để khởi động lại.")

    _record_profile_health(profile_id, "recovering")

    try:
        close_gemlogin_profile(profile_id, api_url, timeout=timeout)
    except Exception as exc:
        clear_gemlogin_debug_address(profile_id)
        _LOGGER.warning(
            "GemLogin close failed while recovering Profile %s; continuing with start: %s",
            profile_id,
            exc,
        )

    delay = max(0.0, float(restart_delay_seconds))
    if delay:
        time.sleep(delay)
    try:
        debug_address = _start_gemlogin_profile(profile_id, api_url, timeout=timeout)
    except Exception as exc:
        _record_profile_health(profile_id, "unhealthy", error=exc)
        raise
    _record_profile_health(
        profile_id,
        "starting",
        debug_address=debug_address,
        recovered=True,
    )
    return debug_address


def run_with_gemlogin_recovery(
    operation,
    gemlogin_profile_id: str,
    api_url: str,
    *,
    operation_name: str = "automation",
    attempts: int = 2,
    retry_delay_seconds: float = 2,
):
    """Retry a safe operation after forcing a fresh profile only for CDP failures."""
    profile_id = str(gemlogin_profile_id or "").strip()
    max_attempts = max(1, int(attempts))
    delay = max(0.0, float(retry_delay_seconds))
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if not is_gemlogin_connection_error(exc):
                raise
            last_error = BrowserUnavailableError(str(exc), cause=exc)
            if attempt >= max_attempts:
                raise last_error from exc
            _LOGGER.warning(
                "%s failed because Profile %s lost CDP on attempt %s/%s; recovering: %s",
                operation_name,
                profile_id,
                attempt,
                max_attempts,
                exc,
            )
            restart_gemlogin_profile(
                profile_id,
                api_url,
                restart_delay_seconds=delay,
            )

    raise BrowserUnavailableError(
        f"{operation_name} failed: {last_error}", cause=last_error
    ) from last_error


def _start_playwright_connection(debug_address: str):
    _ensure_playwright_shared_worker_compatibility()
    manager = sync_playwright()
    playwright = None
    try:
        playwright = manager.start()
        browser = playwright.chromium.connect_over_cdp(cdp_endpoint(debug_address))
        return manager, playwright, browser
    except Exception:
        _stop_playwright_driver(manager, playwright)
        raise


@contextmanager
def connected_gemlogin_profile(
    gemlogin_profile_id: str,
    api_url: str,
    *,
    attempts: int = 3,
    retry_delay_seconds: float = 2,
    cdp_ready_timeout_seconds: float = DEFAULT_CDP_READY_TIMEOUT_SECONDS,
    close_profile_on_exit: bool = False,
):
    """Connect to a GemLogin profile and recover a stale or refused CDP endpoint."""
    profile_id = str(gemlogin_profile_id or "").strip()
    if not profile_id:
        raise RuntimeError("Chưa cấu hình GemLogin Profile ID.")

    max_attempts = max(1, int(attempts))
    delay = max(0.0, float(retry_delay_seconds))
    _record_profile_health(profile_id, "checking")

    with _PLAYWRIGHT_OPERATION_LOCK, _cross_process_playwright_lock():
        manager = None
        playwright = None
        browser = None
        current_address = ""
        last_error = None
        restart_required = False

        for attempt in range(1, max_attempts + 1):
            try:
                if restart_required:
                    current_address = ""
                    current_address = restart_gemlogin_profile(
                        profile_id,
                        api_url,
                        restart_delay_seconds=delay,
                    )
                    restart_required = False
                elif not current_address:
                    current_address = get_gemlogin_debug_address(profile_id, api_url)

                if not _wait_for_cdp(
                    current_address,
                    timeout_seconds=cdp_ready_timeout_seconds,
                ):
                    raise ConnectionError(
                        f"CDP {current_address} của Profile {profile_id} không phản hồi."
                    )

                candidate_manager, candidate_playwright, candidate_browser = (
                    _start_playwright_connection(current_address)
                )
            except Exception as exc:
                last_error = exc
                endpoint_alive = bool(current_address) and _is_cdp_reachable(current_address)
                if current_address and not endpoint_alive:
                    clear_gemlogin_debug_address(profile_id)
                    restart_required = True

                if attempt >= max_attempts:
                    break

                _record_profile_health(
                    profile_id,
                    "recovering" if restart_required else "retrying",
                    debug_address=current_address,
                    error=exc,
                )

                if restart_required:
                    _LOGGER.warning(
                        "CDP Profile %s at %s is unavailable on attempt %s/%s; "
                        "GemLogin will close and reopen this profile: %s",
                        profile_id,
                        current_address or "(unknown)",
                        attempt,
                        max_attempts,
                        exc,
                    )
                else:
                    _LOGGER.warning(
                        "Playwright driver failed for live Profile %s on attempt %s/%s; "
                        "starting a fresh driver: %s",
                        profile_id,
                        attempt,
                        max_attempts,
                        exc,
                    )
                    if delay:
                        time.sleep(delay)
                continue

            manager = candidate_manager
            playwright = candidate_playwright
            browser = candidate_browser
            break

        if browser is None:
            _record_profile_health(
                profile_id,
                "unhealthy",
                debug_address=current_address,
                error=last_error,
            )
            raise RuntimeError(
                f"Không thể phục hồi kết nối GemLogin Profile {profile_id} sau "
                f"{max_attempts} lần; endpoint cuối: {current_address or 'không xác định'}. "
                f"Chi tiết: {last_error}"
            ) from last_error

        _record_profile_health(
            profile_id,
            "healthy",
            debug_address=current_address,
        )
        try:
            yield browser
        except Exception as exc:
            if is_gemlogin_connection_error(exc):
                clear_gemlogin_debug_address(profile_id)
                _record_profile_health(
                    profile_id,
                    "disconnected",
                    debug_address=current_address,
                    error=exc,
                )
            raise
        finally:
            try:
                if not browser.is_connected():
                    clear_gemlogin_debug_address(profile_id)
                    _record_profile_health(
                        profile_id,
                        "disconnected",
                        debug_address=current_address,
                        error="Browser CDP disconnected during operation.",
                    )
            except Exception:
                pass
            _stop_playwright_driver(manager, playwright)
            if close_profile_on_exit:
                try:
                    close_gemlogin_profile(profile_id, api_url)
                    clear_gemlogin_debug_address(profile_id)
                    _record_profile_health(profile_id, "closed")
                except Exception as exc:
                    _LOGGER.warning(
                        "Không thể đóng GemLogin Profile %s sau lượt quét: %s",
                        profile_id,
                        exc,
                    )


def create_background_page(browser, context, *, timeout_ms: int = 15000):
    """Create a Chromium page without activating its tab or browser window."""
    session = browser.new_browser_cdp_session()
    try:
        with context.expect_page(timeout=timeout_ms) as page_info:
            session.send(
                "Target.createTarget",
                {
                    "url": "about:blank",
                    "background": True,
                },
            )
        page = page_info.value
        page.wait_for_load_state("commit", timeout=timeout_ms)
        return page
    except Exception as exc:
        raise RuntimeError(
            "Không thể tạo tab automation ở chế độ nền; đã dừng để tránh giành focus."
        ) from exc
    finally:
        try:
            session.detach()
        except Exception:
            pass
