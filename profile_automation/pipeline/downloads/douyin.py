from __future__ import annotations

import json
import os
import time
from collections import deque
from contextlib import ExitStack
from typing import Optional
from urllib.parse import unquote

import core.config as config
from core.utils import console, logger
from profile_automation.pipeline.downloads.captions import get_tracking_download_path
from profile_automation.watchers.douyin_video import (
    DouyinVideo,
    extract_douyin_download_urls,
)
from services.browser.browser_profile_service import (
    connected_browser_profile as connected_gemlogin_profile,
    configure_lightweight_scan_page,
    create_background_page,
    is_browser_connection_error as is_gemlogin_connection_error,
    run_with_browser_recovery as run_with_gemlogin_recovery,
)
from services.integrations.diagnostic_artifact_service import attach_response_trace, record_browser_diagnostic
from services.integrations.telegram_service import send_diagnostic_notification, send_error_notification
from services.publishing.video_validation_service import validate_video_file
from services.runtime.workload_coordinator import WorkloadCancelled, workload_slot

MAX_DOWNLOAD_PAGE_RELOADS = 3
DIRECT_DOWNLOAD_RETRY_DELAY_SECONDS = 2
DOUYIN_BRIDGE_VERSION = "1.2.0"
DOUYIN_DIRECT_RESULT_TIMEOUT_SECONDS = 15
DOUYIN_EXTENSION_FALLBACK_TIMEOUT_SECONDS = 10

def _close_page_quietly(page) -> None:
    if page is None:
        return
    try:
        if not page.is_closed():
            page.close()
    except Exception:
        pass


def _is_valid_download_result(result: object) -> bool:
    return bool(
        isinstance(result, dict)
        and result.get("status") == "success"
        and str(result.get("download_url") or "").startswith(("http://", "https://"))
    )


def _download_result_from_payload(payload: object, aweme_id: str, source: str) -> Optional[dict]:
    expected_id = str(aweme_id or "")
    queue = deque([payload])
    visited = 0
    while queue and visited < 15000:
        current = queue.popleft()
        visited += 1
        if isinstance(current, dict):
            current_id = str(
                current.get("aweme_id") or current.get("awemeId") or current.get("id") or ""
            )
            if current_id == expected_id:
                download_urls = extract_douyin_download_urls(current)
                if download_urls:
                    return {
                        "status": "success",
                        "source": source,
                        "aweme_id": expected_id,
                        "video_id": None,
                        "download_url": download_urls[0],
                        "download_urls": download_urls,
                        "headers_required": False,
                        "headers": {},
                    }
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
    return None


def _install_douyin_response_capture(page, aweme_id: str) -> dict:
    captured: dict = {}

    def capture_response(response) -> None:
        try:
            url = str(response.url or "")
            normalized_url = url.lower()
            if not any(
                token in normalized_url
                for token in ("aweme", "feed", "detail", "search", "discover", "recommend")
            ):
                return
            content_type = str(response.headers.get("content-type", "")).lower()
            if "json" not in content_type:
                return
            result = _download_result_from_payload(
                response.json(),
                aweme_id,
                source=f"playwright_response:{url}",
            )
            if result:
                captured.clear()
                captured.update(result)
        except Exception:
            pass

    page.on("response", capture_response)
    return captured


def _candidate_video_page_urls(video: DouyinVideo) -> list[str]:
    urls = [f"https://www.douyin.com/video/{video.aweme_id}"]
    share_url = str(video.share_url or "").strip()
    if share_url.startswith(("http://", "https://")) and share_url not in urls:
        urls.append(share_url)
    return urls


def _json_payload_from_text(value: object) -> Optional[object]:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, unquote(text)):
        try:
            return json.loads(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _extract_douyin_ssr_result(page, aweme_id: str) -> Optional[dict]:
    """Read server-rendered Douyin state without relying on an extension."""
    try:
        serialized_states = page.evaluate(
            """() => {
                const stringify = (value) => {
                    try { return value ? JSON.stringify(value) : ''; }
                    catch (_) { return ''; }
                };
                return {
                    render_data: document.querySelector('#RENDER_DATA')?.textContent || '',
                    initial_state: stringify(window.__INITIAL_STATE__),
                    init_props: stringify(window.__INIT_PROPS__)
                };
            }"""
        )
    except Exception:
        return None

    if not isinstance(serialized_states, dict):
        return None
    for source_name, value in serialized_states.items():
        payload = _json_payload_from_text(value)
        if payload is None:
            continue
        result = _download_result_from_payload(
            payload,
            aweme_id,
            source=f"playwright_ssr:{source_name}",
        )
        if result:
            return result
    return None


def _wait_for_douyin_direct_result(
    page,
    aweme_id: str,
    timeout_seconds: int = DOUYIN_DIRECT_RESULT_TIMEOUT_SECONDS,
    captured_result: Optional[dict] = None,
) -> dict:
    """Prefer Playwright response and SSR data; no extension is required."""
    if _is_valid_download_result(captured_result):
        return dict(captured_result)

    deadline = time.monotonic() + max(0.1, timeout_seconds)
    while time.monotonic() < deadline:
        if _is_valid_download_result(captured_result):
            return dict(captured_result)
        ssr_result = _extract_douyin_ssr_result(page, aweme_id)
        if _is_valid_download_result(ssr_result):
            return ssr_result
        try:
            page.wait_for_timeout(300)
        except Exception:
            time.sleep(0.3)

    if _is_valid_download_result(captured_result):
        return dict(captured_result)
    raise RuntimeError(
        f"Playwright không tìm được link video {aweme_id} trong response JSON hoặc dữ liệu SSR "
        f"sau {timeout_seconds} giây."
    )


def _try_douyin_extension_result(
    page,
    aweme_id: str,
    timeout_seconds: int = DOUYIN_EXTENSION_FALLBACK_TIMEOUT_SECONDS,
) -> Optional[dict]:
    """Use the bridge only when direct Playwright extraction did not return a URL."""
    try:
        health = page.evaluate(
            """() => Boolean(
                window.__AixDouyinAutomation &&
                typeof window.__AixDouyinAutomation.getByAwemeId === 'function'
            )"""
        )
    except Exception:
        return None
    if not health:
        return None

    try:
        page.evaluate("() => window.__AixDouyinAutomation.rescan()")
    except Exception:
        pass

    deadline = time.monotonic() + max(1, timeout_seconds)
    while time.monotonic() < deadline:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        try:
            handle = page.wait_for_function(
                """(expectedId) => {
                    const item = window.__AixDouyinAutomation?.getByAwemeId(expectedId);
                    return item?.status === 'success' && item?.download_url ? item : false;
                }""",
                arg=str(aweme_id),
                timeout=min(1000, remaining_ms),
            )
            try:
                result = handle.json_value()
            finally:
                handle.dispose()
            if _is_valid_download_result(result):
                return result
        except Exception:
            pass
    return None


def _wait_for_douyin_download_result(
    page,
    aweme_id: str,
    timeout_seconds: int = 30,
    captured_result: Optional[dict] = None,
) -> dict:
    direct_timeout = min(max(1, timeout_seconds), DOUYIN_DIRECT_RESULT_TIMEOUT_SECONDS)
    direct_error = None
    try:
        return _wait_for_douyin_direct_result(
            page,
            aweme_id,
            timeout_seconds=direct_timeout,
            captured_result=captured_result,
        )
    except Exception as exc:
        direct_error = exc

    if _is_valid_download_result(captured_result):
        return dict(captured_result)
    extension_timeout = min(
        max(1, timeout_seconds - direct_timeout),
        DOUYIN_EXTENSION_FALLBACK_TIMEOUT_SECONDS,
    )
    extension_result = _try_douyin_extension_result(
        page,
        aweme_id,
        timeout_seconds=extension_timeout,
    )
    if _is_valid_download_result(extension_result):
        return extension_result

    if _is_valid_download_result(captured_result):
        return dict(captured_result)
    raise RuntimeError(
        f"Không tìm được link tải trực tiếp cho video {aweme_id} "
        "từ feed, response JSON hoặc SSR; extension dự phòng cũng không trả về link. "
        f"Chi tiết: {direct_error}"
    ) from direct_error


def check_douyin_direct_download(
    gemlogin_profile_id: str,
    api_url: str = config.API_URL,
    timeout_seconds: int = 15,
    profile_config: Optional[dict] = None,
    *,
    page: Optional[object] = None,
) -> dict:
    """Check GemLogin/CDP and report extension health as optional metadata."""
    if page is not None:
        try:
            configure_lightweight_scan_page(page)
            if not getattr(page, "url", "").startswith("https://www.douyin.com"):
                page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=60000)
            health = page.evaluate(
                """() => {
                    const bridge = window.__AixDouyinAutomation;
                    if (!bridge || typeof bridge.health !== 'function') return null;
                    try { return bridge.health(); }
                    catch (_) { return null; }
                }""",
            )
            version = str(health.get("version") or "") if isinstance(health, dict) else ""
            extension_ready = bool(
                isinstance(health, dict)
                and health.get("status") == "ready"
                and version == DOUYIN_BRIDGE_VERSION
            )
            if extension_ready:
                message = (
                    f"Tải trực tiếp sẵn sàng; extension dự phòng v{version} đang hoạt động."
                )
            elif version:
                message = (
                    f"Tải trực tiếp sẵn sàng; extension dự phòng v{version} khác phiên bản "
                    f"{DOUYIN_BRIDGE_VERSION} nhưng không bắt buộc."
                )
            else:
                message = "Tải trực tiếp sẵn sàng; không cần cài extension."
            return {
                "ok": True,
                "version": version,
                "mode": "direct_with_extension_fallback" if extension_ready else "direct_only",
                "extension_ready": extension_ready,
                "message": message,
            }
        except Exception as exc:
            return {
                "ok": False,
                "version": "",
                "mode": "unavailable",
                "extension_ready": False,
                "message": f"Không thể kết nối profile trình duyệt để tải trực tiếp. Chi tiết: {exc}",
            }

    managed_page = None
    try:
        with connected_gemlogin_profile(
            gemlogin_profile_id,
            api_url,
            profile_config=profile_config,
        ) as browser, ExitStack() as page_cleanup:
            if not browser.contexts:
                raise RuntimeError("GemLogin không có browser context.")

            managed_page = create_background_page(browser, browser.contexts[0])
            page_cleanup.callback(_close_page_quietly, managed_page)
            configure_lightweight_scan_page(managed_page)
            managed_page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=60000)
            health = managed_page.evaluate(
                """() => {
                    const bridge = window.__AixDouyinAutomation;
                    if (!bridge || typeof bridge.health !== 'function') return null;
                    try { return bridge.health(); }
                    catch (_) { return null; }
                }""",
            )
            version = str(health.get("version") or "") if isinstance(health, dict) else ""
            extension_ready = bool(
                isinstance(health, dict)
                and health.get("status") == "ready"
                and version == DOUYIN_BRIDGE_VERSION
            )
            if extension_ready:
                message = (
                    f"Tải trực tiếp sẵn sàng; extension dự phòng v{version} đang hoạt động."
                )
            elif version:
                message = (
                    f"Tải trực tiếp sẵn sàng; extension dự phòng v{version} khác phiên bản "
                    f"{DOUYIN_BRIDGE_VERSION} nhưng không bắt buộc."
                )
            else:
                message = "Tải trực tiếp sẵn sàng; không cần cài extension."
            return {
                "ok": True,
                "version": version,
                "mode": "direct_with_extension_fallback" if extension_ready else "direct_only",
                "extension_ready": extension_ready,
                "message": message,
            }
    except Exception as exc:
        return {
            "ok": False,
            "version": "",
            "mode": "unavailable",
            "extension_ready": False,
            "message": f"Không thể kết nối profile trình duyệt để tải trực tiếp. Chi tiết: {exc}",
        }
    finally:
        _close_page_quietly(managed_page)


def _download_douyin_url(
    context,
    page,
    result: dict,
    save_path: str,
    platform_label: str = "Douyin",
) -> str:
    import requests

    started_at = time.monotonic()
    download_url = str(result["download_url"])
    user_agent = str(result.get("user_agent") or page.evaluate("() => navigator.userAgent"))
    referer = str(result.get("referer") or page.url or "https://www.douyin.com/")
    headers = {
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Referer": referer,
        "User-Agent": user_agent,
    }
    extra_headers = result.get("headers")
    if isinstance(extra_headers, dict):
        headers.update({str(key): str(value) for key, value in extra_headers.items() if value is not None})

    cookies = {
        item["name"]: item["value"]
        for item in context.cookies([download_url])
        if item.get("name")
    }
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    partial_path = f"{save_path}.part"
    try:
        if os.path.exists(partial_path):
            os.remove(partial_path)
        bytes_written = 0
        with requests.get(
            download_url,
            headers=headers,
            cookies=cookies,
            stream=True,
            allow_redirects=True,
            timeout=(20, 180),
        ) as response:
            response.raise_for_status()
            content_type = str(response.headers.get("content-type", "")).lower()
            if "text/html" in content_type or "application/json" in content_type:
                raise RuntimeError(f"Máy chủ trả về {content_type or 'nội dung không phải video'}.")
            with open(partial_path, "wb") as file_handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    file_handle.write(chunk)
                    bytes_written += len(chunk)
                file_handle.flush()
                os.fsync(file_handle.fileno())
        if bytes_written < 1024:
            raise RuntimeError(f"File tải về quá nhỏ ({bytes_written} byte).")

        # Validate before committing the atomic download. Douyin can return a
        # successful, non-HTML response that contains only audio. Treat that as
        # a bad candidate URL so the caller can try another rendition.
        media_info = validate_video_file(partial_path, require_audio=True)
        os.replace(partial_path, save_path)
        elapsed = max(time.monotonic() - started_at, 0.001)
        logger.info(
            "[Tải trực tiếp %s] Đã tải và xác thực %s byte, %sx%s, %.1f giây "
            "trong %.1f giây (%.1f KB/giây): %s",
            platform_label,
            bytes_written,
            media_info["width"],
            media_info["height"],
            media_info["duration_seconds"],
            elapsed,
            bytes_written / elapsed / 1024,
            save_path,
        )
        return save_path
    except Exception:
        try:
            if os.path.exists(partial_path):
                os.remove(partial_path)
        except OSError:
            pass
        raise


def _download_urls_from_result(result: dict) -> list[str]:
    candidates = [result.get("download_url")]
    extra_candidates = result.get("download_urls")
    if isinstance(extra_candidates, (list, tuple)):
        candidates.extend(extra_candidates)
    return list(
        dict.fromkeys(
            str(candidate).strip()
            for candidate in candidates
            if str(candidate or "").startswith(("http://", "https://"))
        )
    )


def _download_douyin_candidates(
    context,
    page,
    result: dict,
    save_path: str,
    platform_label: str = "Douyin",
) -> str:
    candidates = _download_urls_from_result(result)
    if not candidates:
        raise RuntimeError(f"Kết quả {platform_label} không chứa URL tải video hợp lệ.")

    errors = []
    for index, download_url in enumerate(candidates, start=1):
        candidate_result = dict(result)
        candidate_result["download_url"] = download_url
        try:
            return _download_douyin_url(
                context,
                page,
                candidate_result,
                save_path,
                platform_label=platform_label,
            )
        except Exception as exc:
            if is_gemlogin_connection_error(exc):
                raise
            errors.append(f"URL {index}/{len(candidates)}: {exc}")
            logger.warning(
                "URL video %s %s/%s không hợp lệ; thử URL kế tiếp: %s",
                platform_label,
                index,
                len(candidates),
                exc,
            )

    raise RuntimeError(
        f"Không URL video {platform_label} nào vượt qua kiểm tra media. "
        + " | ".join(errors)
    )


def _download_douyin_video_direct_once(
    video: DouyinVideo,
    profile_id: str,
    gemlogin_profile_id: str,
    api_url: str = config.API_URL,
    profile_config: Optional[dict] = None,
) -> Optional[str]:
    """Download from feed/Playwright data, with the extension as an optional fallback."""
    save_path = get_tracking_download_path(profile_id, video.aweme_id)
    page = None
    response_trace: dict = {}
    try:
        with connected_gemlogin_profile(
            gemlogin_profile_id,
            api_url,
            profile_config=profile_config,
        ) as browser, ExitStack() as page_cleanup:
            if not browser.contexts:
                raise RuntimeError("GemLogin không có browser context để tải video Douyin.")

            context = browser.contexts[0]
            page = create_background_page(browser, context)
            page_cleanup.callback(_close_page_quietly, page)
            response_trace = attach_response_trace(page)
            captured_result = _install_douyin_response_capture(page, video.aweme_id)
            navigation_urls = _candidate_video_page_urls(video)
            total_attempts = MAX_DOWNLOAD_PAGE_RELOADS + 1
            last_error = ""

            feed_download_url = str(getattr(video, "download_url", "") or "").strip()
            if feed_download_url.startswith(("http://", "https://")):
                try:
                    feed_download_urls = list(getattr(video, "download_urls", []) or [])
                    feed_result = {
                        "status": "success",
                        "source": "profile_feed",
                        "aweme_id": video.aweme_id,
                        "download_url": feed_download_url,
                        "download_urls": feed_download_urls,
                        "referer": video.share_url,
                        "headers_required": False,
                        "headers": {},
                    }
                    console.print(
                        f"[cyan]   → Đang tải video {video.aweme_id} bằng URL đã bắt từ feed...[/]"
                    )
                    return _download_douyin_candidates(context, page, feed_result, save_path)
                except Exception as feed_error:
                    last_error = str(feed_error)
                    logger.warning(
                        "[Profile %s] URL từ feed của video %s không còn dùng được; "
                        "chuyển sang làm mới URL từ trang: %s",
                        profile_id,
                        video.aweme_id,
                        feed_error,
                    )

            for attempt_index in range(total_attempts):
                try:
                    target_url = navigation_urls[attempt_index % len(navigation_urls)]
                    captured_result.clear()
                    if page.url == target_url:
                        page.reload(wait_until="domcontentloaded", timeout=60000)
                    else:
                        page.goto(target_url, wait_until="domcontentloaded", timeout=60000)

                    console.print(
                        f"[cyan]   → Đang chờ link trực tiếp video {video.aweme_id} "
                        f"(lượt {attempt_index + 1}/{total_attempts})...[/]"
                    )
                    result = _wait_for_douyin_download_result(
                        page,
                        video.aweme_id,
                        captured_result=captured_result,
                    )
                    console.print("[dim]   → Đã nhận link trực tiếp; bắt đầu tải nền, không click tọa độ.[/]")
                    downloaded_path = _download_douyin_candidates(
                        context,
                        page,
                        result,
                        save_path,
                    )
                    console.print(f"[green]   → Đã tải xong video: {downloaded_path}[/]")
                    return downloaded_path
                except Exception as attempt_error:
                    last_error = str(attempt_error)
                    logger.warning(
                        "[Profile %s] Lấy/tải link trực tiếp video %s thất bại lượt %s/%s: %s",
                        profile_id,
                        video.aweme_id,
                        attempt_index + 1,
                        total_attempts,
                        attempt_error,
                    )
                    if is_gemlogin_connection_error(attempt_error):
                        raise
                    if attempt_index < total_attempts - 1:
                        time.sleep(DIRECT_DOWNLOAD_RETRY_DELAY_SECONDS)

            error_message = (
                f"Không thể lấy hoặc tải link trực tiếp từ feed, response JSON hoặc SSR Douyin "
                f"sau {total_attempts} lượt. "
                f"Chi tiết: {last_error}"
            )
            raise RuntimeError(error_message)
    except Exception as exc:
        diagnostic_record = record_browser_diagnostic(
            page=page,
            profile_id=profile_id,
            video_id=str(video.aweme_id),
            platform="douyin",
            error=exc,
            url=str(getattr(video, "share_url", "") or ""),
            last_response=response_trace,
            send_telegram=False,
        )
        try:
            setattr(exc, "diagnostic_record", diagnostic_record)
        except Exception:
            pass
        logger.exception(
            "[Profile %s] Lỗi tải trực tiếp video Douyin %s: %s",
            profile_id,
            video.aweme_id,
            exc,
        )
        raise
    finally:
        if page is not None:
            _close_page_quietly(page)


def download_douyin_video_direct(
    video: DouyinVideo,
    profile_id: str,
    gemlogin_profile_id: str,
    api_url: str = config.API_URL,
    *,
    priority: int = 100,
    cancel_event=None,
    profile_config: Optional[dict] = None,
) -> Optional[str]:
    """Download once, recovering and repeating the step only when CDP disconnects."""
    try:
        with workload_slot(
            "download",
            profile_id=profile_id,
            video_id=video.aweme_id,
            priority=priority,
            cancel_event=cancel_event,
        ):
            return run_with_gemlogin_recovery(
                lambda: _download_douyin_video_direct_once(
                    video,
                    profile_id,
                    gemlogin_profile_id,
                    api_url,
                    profile_config,
                ),
                gemlogin_profile_id,
                api_url,
                operation_name=f"Tải video Douyin {video.aweme_id}",
                attempts=2,
                profile_config=profile_config,
            )
    except WorkloadCancelled:
        raise
    except Exception as exc:
        error_message = f"Lỗi tải trực tiếp video Douyin: {exc}"
        send_error_notification(
            error_message,
            profile_id=profile_id,
            video_id=video.aweme_id,
        )
        diagnostic_record = getattr(exc, "diagnostic_record", None)
        if isinstance(diagnostic_record, dict):
            send_diagnostic_notification(diagnostic_record)
        return None
