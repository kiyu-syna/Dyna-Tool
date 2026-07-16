import os
import time
import copy
import json
from collections import deque
from contextlib import ExitStack
from typing import Optional
from urllib.parse import unquote
from core.utils import logger, console
import core.config as config
from profile_automation.watchers.douyin_profile_monitor import (
    DouyinProfileMonitor,
    DouyinVideo,
    extract_douyin_download_urls,
)
from profile_automation.pipeline.upload_pipeline import (
    UploadPipeline,
    all_enabled_uploads_succeeded,
    enabled_platform_names,
)
from profile_automation.pipeline.video_job_store import TERMINAL_STATUSES, VideoJobStore
from profile_automation.douyin_sources import get_douyin_sources, MAX_NEW_VIDEOS_PER_SOURCE
from services.telegram_service import (
    request_caption_for_video,
    send_diagnostic_notification,
    send_error_notification,
    send_new_video_notification,
    send_video_upload_summary_notification,
)
from services.diagnostic_artifact_service import (
    attach_response_trace,
    record_browser_diagnostic,
)
from services.busy_mode_service import is_busy_mode_enabled
from services.gemlogin_browser_service import (
    connected_gemlogin_profile,
    create_background_page,
    is_gemlogin_connection_error,
    run_with_gemlogin_recovery,
)
from services.video_validation_service import (
    FFprobeNotFoundError,
    InvalidVideoFileError,
    validate_video_file,
)
from services.workload_coordinator import (
    WorkloadCancelled,
    priority_for_job,
    workload_slot,
)

PLATFORM_CAPTION_DEFAULTS = {
    "tiktok": True,
    "facebook": False,
    "youtube": False,
}

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


def get_tracking_download_path(profile_id: str, video_id: str) -> str:
    save_dir = os.path.join(
        os.path.expanduser("~"),
        "Videos",
        "Tracking Douyin",
        f"Profile {profile_id}",
    )
    filename = f"[Profile {profile_id}]_{video_id}.mp4"
    return os.path.normpath(os.path.join(save_dir, filename))


def should_request_caption(profile: dict) -> bool:
    if is_busy_mode_enabled():
        return False
    for platform_key, enabled_default in PLATFORM_CAPTION_DEFAULTS.items():
        cfg = profile.get(platform_key, {}) or {}
        if cfg.get("enabled", enabled_default) and not cfg.get("use_original_desc", False):
            return True
    return False


def resolve_runtime_caption(
    profile: dict,
    profile_id: str,
    video: DouyinVideo,
    source_label: str = "",
) -> Optional[str]:
    if is_busy_mode_enabled():
        caption = str(profile.get("default_caption") or "").strip()
        if not caption:
            caption = str(getattr(video, "desc", "") or "").strip()
        logger.info(
            "[Profile %s] Chế độ Bận đang bật; dùng mô tả mặc định cho video %s.",
            profile_id,
            video.aweme_id,
        )
        return caption
    if not should_request_caption(profile):
        logger.info(
            f"[Profile {profile_id}] Bo qua Telegram caption cho video {video.aweme_id} "
            f"vi tat ca nen tang dang bat deu dang dung mo ta goc."
        )
        return getattr(video, "desc", "") or ""
    return request_caption_for_video(
        profile_id,
        video,
        default_caption=str(profile.get("default_caption") or ""),
        timeout_sec=10 * 60,
        profile_name=str(profile.get("name") or ""),
        source_label=source_label,
        platforms=enabled_platform_names(profile),
    )


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


def _wait_for_douyin_bridge_result(
    page,
    aweme_id: str,
    timeout_seconds: int = 30,
    captured_result: Optional[dict] = None,
) -> dict:
    """Backward-compatible name for the direct-first resolver."""
    return _wait_for_douyin_download_result(
        page,
        aweme_id,
        timeout_seconds=timeout_seconds,
        captured_result=captured_result,
    )


def check_douyin_direct_download(
    gemlogin_profile_id: str,
    api_url: str = config.API_URL,
    timeout_seconds: int = 15,
) -> dict:
    """Check GemLogin/CDP and report extension health as optional metadata."""
    page = None
    try:
        with connected_gemlogin_profile(
            gemlogin_profile_id,
            api_url,
        ) as browser, ExitStack() as page_cleanup:
            if not browser.contexts:
                raise RuntimeError("GemLogin không có browser context.")

            page = create_background_page(browser, browser.contexts[0])
            page_cleanup.callback(_close_page_quietly, page)
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
            "message": f"Không thể kết nối GemLogin/CDP để tải trực tiếp. Chi tiết: {exc}",
        }
    finally:
        _close_page_quietly(page)


def check_douyin_extension_bridge(
    gemlogin_profile_id: str,
    api_url: str = config.API_URL,
    timeout_seconds: int = 15,
) -> dict:
    """Backward-compatible alias; an extension is no longer required."""
    return check_douyin_direct_download(
        gemlogin_profile_id,
        api_url=api_url,
        timeout_seconds=timeout_seconds,
    )


def _download_douyin_url(context, page, result: dict, save_path: str) -> str:
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
            "[Douyin Direct Download] Đã tải và xác thực %s byte, %sx%s, %.1fs "
            "trong %.1fs (%.1f KB/s): %s",
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


def _download_douyin_candidates(context, page, result: dict, save_path: str) -> str:
    candidates = _download_urls_from_result(result)
    if not candidates:
        raise RuntimeError("Kết quả Douyin không chứa URL tải video hợp lệ.")

    errors = []
    for index, download_url in enumerate(candidates, start=1):
        candidate_result = dict(result)
        candidate_result["download_url"] = download_url
        try:
            return _download_douyin_url(context, page, candidate_result, save_path)
        except Exception as exc:
            if is_gemlogin_connection_error(exc):
                raise
            errors.append(f"URL {index}/{len(candidates)}: {exc}")
            logger.warning(
                "URL video Douyin %s/%s không hợp lệ; thử URL kế tiếp: %s",
                index,
                len(candidates),
                exc,
            )

    raise RuntimeError(
        "Không URL video Douyin nào vượt qua kiểm tra media. " + " | ".join(errors)
    )


def _download_douyin_video_direct_once(
    video: DouyinVideo,
    profile_id: str,
    gemlogin_profile_id: str,
    api_url: str = config.API_URL,
) -> Optional[str]:
    """Download from feed/Playwright data, with the extension as an optional fallback."""
    save_path = get_tracking_download_path(profile_id, video.aweme_id)
    page = None
    response_trace: dict = {}
    try:
        with connected_gemlogin_profile(
            gemlogin_profile_id,
            api_url,
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
                ),
                gemlogin_profile_id,
                api_url,
                operation_name=f"Tải video Douyin {video.aweme_id}",
                attempts=2,
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


def download_video_via_extension(
    video: DouyinVideo,
    profile_id: str,
    gemlogin_profile_id: str,
    api_url: str = config.API_URL,
) -> Optional[str]:
    """Backward-compatible alias for the extension-independent downloader."""
    return download_douyin_video_direct(
        video,
        profile_id,
        gemlogin_profile_id,
        api_url=api_url,
    )


def download_video_via_so9(share_url: str, save_dir: str) -> Optional[str]:
    """
    Tải video Douyin không watermark qua SO9 Downloader sử dụng Playwright (đồng bộ).
    Trả về đường dẫn tuyệt đối của file video tải về thành công, hoặc None.
    """
    from playwright.sync_api import sync_playwright
    
    os.makedirs(save_dir, exist_ok=True)
    downloader_url = "https://so9.vn/9downloader/douyin"
    
    with sync_playwright() as pw:
        browser = None
        launch_args = ["--disable-blink-features=AutomationControlled"]
        for channel in ("chrome", "msedge", None):
            try:
                browser = pw.chromium.launch(
                    headless=True,
                    channel=channel if channel else None,
                    args=launch_args,
                )
                break
            except Exception:
                continue
                
        if not browser:
            logger.error("Không khởi động được trình duyệt để tải video.")
            return None
            
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        
        try:
            logger.info(f"Đang mở SO9 downloader: {downloader_url}")
            page.goto(downloader_url, timeout=45000, wait_until="domcontentloaded")
            
            # Tìm ô input
            input_sel = None
            selectors = [
                ".download-input-field input[type='text']",
                ".download-input-field textarea",
                "input[placeholder*='link' i]",
                "input[placeholder*='url' i]",
                "input[type='text']:not([type='hidden'])",
            ]
            for sel in selectors:
                try:
                    if page.locator(sel).first.is_visible(timeout=2000):
                        input_sel = sel
                        break
                except:
                    continue
                    
            if not input_sel:
                logger.error("Không tìm thấy ô nhập link trên SO9.")
                return None
                
            # Điền link
            page.fill(input_sel, share_url)
            time.sleep(1)
            
            # Tìm nút Tải
            btn_sel = None
            candidates = [
                ".download-input-field .origin-button",
                ".download-input-field button",
                "button:has-text('Tải')",
                "button:has-text('Download')",
            ]
            for sel in candidates:
                try:
                    if page.locator(sel).first.is_visible(timeout=2000):
                        btn_sel = sel
                        break
                except:
                    continue
                    
            if not btn_sel:
                logger.error("Không tìm thấy nút tải xuống trên SO9.")
                return None
                
            old_url = page.url
            page.click(btn_sel)
            
            # Đợi URL thay đổi hoặc chờ kết quả
            timeout = time.time() + 15
            while page.url == old_url and time.time() < timeout:
                time.sleep(0.5)
                
            # Chờ nút cloud download kết quả xuất hiện (tối đa 45s)
            download_icon_sel = "i.bx.bxs-cloud-download"
            try:
                page.wait_for_selector(download_icon_sel, timeout=45000)
                logger.info("Đã xuất hiện nút tải, bắt đầu download...")
                
                with page.expect_download(timeout=60000) as dl_info:
                    page.click(download_icon_sel)
                    
                download = dl_info.value
                filename = f"video_{int(time.time())}.mp4"
                save_path = os.path.normpath(os.path.join(save_dir, filename))
                
                download.save_as(save_path)
                logger.info(f"Tải video thành công: {save_path}")
                return save_path
                
            except Exception as e:
                logger.error(f"Lỗi khi chờ tải file từ SO9: {e}")
                return None
                
        except Exception as e:
            logger.error(f"Lỗi tiến trình tải video qua SO9: {e}")
            return None
        finally:
            context.close()
            browser.close()


class ProfileWorker:
    def __init__(self, profile_config: dict):
        self.profile = profile_config
        self.profile_id = str(profile_config.get("id"))
        self.name = profile_config.get("name", "Unnamed Profile")

        douyin_cfg = profile_config.get("douyin", {})
        self.gemlogin_id = str(douyin_cfg.get("gemlogin_profile_id") or self.profile_id)
        self.sources = get_douyin_sources(profile_config)

        filters = profile_config.get("filters", {})
        self.min_likes = filters.get("min_likes", 0)
        self.max_duration = filters.get("max_duration_seconds", 300)

        self.pipeline = UploadPipeline()
        self.job_store = VideoJobStore()

        self.save_dir = profile_config.get("save_dir")
        if not self.save_dir:
            self.save_dir = os.path.join(config.BASE_DIR, "Downloads", f"profile_{self.profile_id}")

    def _create_monitor(self, source: dict) -> DouyinProfileMonitor:
        return DouyinProfileMonitor(
            profile_id=self.profile_id,
            sec_uid=source["target_sec_uid"],
            gemlogin_profile_id=self.gemlogin_id,
            api_url=config.API_URL,
            min_likes=self.min_likes,
            max_duration_sec=self.max_duration,
            source_key=source["source_key"],
            migrate_legacy_state=source.get("migrate_legacy_state", False),
        )

    @staticmethod
    def _source_label(source: dict) -> str:
        sec_uid = source.get("target_sec_uid", "")
        return source.get("target_display_name") or f"...{sec_uid[-10:]}"

    def get_pending_videos(self, source: dict) -> list[DouyinVideo]:
        return self.job_store.list_pending_videos(
            self.profile_id,
            source_key=source.get("source_key", ""),
        )

    def register_video(self, video: DouyinVideo, source: dict) -> dict:
        return self.job_store.ensure_job(
            self.profile_id,
            video,
            self.profile,
            source_key=source.get("source_key", ""),
            source_label=self._source_label(source),
        )

    def mark_ignored_job(
        self,
        video: DouyinVideo,
        source: dict,
        monitor: DouyinProfileMonitor,
        reason: str,
    ) -> None:
        self.register_video(video, source)
        self.job_store.set_status(self.profile_id, video.aweme_id, "ignored", error=reason)
        monitor.mark_ignored(video)

    def process_video(
        self,
        video: DouyinVideo,
        source: dict,
        monitor: DouyinProfileMonitor,
        stop_event=None,
    ) -> dict:
        self.register_video(video, source)
        job = self.job_store.get_job(self.profile_id, video.aweme_id) or {}
        if job.get("status") in TERMINAL_STATUSES:
            if job.get("status") == "completed":
                monitor.mark_processed(video)
            elif job.get("status") in {"cancelled", "ignored"}:
                monitor.mark_ignored(video)
            return {"status": job.get("status"), "results": {}}

        if not enabled_platform_names(self.profile):
            reason = "Không có nền tảng đăng video nào đang bật."
            self.job_store.set_status(
                self.profile_id,
                video.aweme_id,
                "ignored",
                error=reason,
            )
            monitor.mark_ignored(video)
            return {"status": "ignored", "results": {}, "error": reason}

        if not self.job_store.claim(self.profile_id, video.aweme_id):
            logger.info(
                "[Profile %s] Video %s đang được một luồng khác xử lý.",
                self.profile_id,
                video.aweme_id,
            )
            return {"status": "busy", "results": {}}

        video_path = ""
        try:
            job = self.job_store.get_job(self.profile_id, video.aweme_id) or {}
            work_priority = priority_for_job(job, self.profile)
            if job.get("caption_resolved"):
                selected_caption = str(job.get("caption") or "")
            else:
                self.job_store.set_status(
                    self.profile_id,
                    video.aweme_id,
                    "waiting_caption",
                    increment_attempt="caption",
                )
                selected_caption = resolve_runtime_caption(
                    self.profile,
                    self.profile_id,
                    video,
                    source_label=self._source_label(source),
                )
                if selected_caption is None:
                    self.job_store.set_status(
                        self.profile_id,
                        video.aweme_id,
                        "cancelled",
                        error="Người dùng hủy video qua Telegram.",
                    )
                    monitor.mark_ignored(video)
                    return {"status": "cancelled", "results": {}}
                self.job_store.set_caption(self.profile_id, video.aweme_id, selected_caption)

            profile_for_upload = copy.deepcopy(self.profile)
            profile_for_upload["_runtime_caption"] = selected_caption

            job = self.job_store.get_job(self.profile_id, video.aweme_id) or {}
            saved_path = str(job.get("download_path") or "")
            deterministic_path = get_tracking_download_path(self.profile_id, video.aweme_id)
            for candidate in (saved_path, deterministic_path):
                if candidate and os.path.isfile(candidate):
                    video_path = candidate
                    break

            if not video_path:
                self.job_store.set_status(
                    self.profile_id,
                    video.aweme_id,
                    "downloading",
                    increment_attempt="download",
                )
                video_path = download_douyin_video_direct(
                    video,
                    self.profile_id,
                    self.gemlogin_id,
                    priority=work_priority,
                    cancel_event=stop_event,
                ) or ""
                if not video_path or not os.path.isfile(video_path):
                    error = "Không thể tải trực tiếp video Douyin."
                    self.job_store.set_status(
                        self.profile_id,
                        video.aweme_id,
                        "failed_download",
                        error=error,
                    )
                    return {"status": "failed_download", "results": {}, "error": error}

            self.job_store.set_download_path(self.profile_id, video.aweme_id, video_path)
            try:
                media_info = validate_video_file(video_path, require_audio=True)
                self.job_store.set_media_info(self.profile_id, video.aweme_id, media_info)
                logger.info(
                    "[Profile %s] Video %s hợp lệ: %sx%s, %.1fs, audio=%s, %s byte.",
                    self.profile_id,
                    video.aweme_id,
                    media_info["width"],
                    media_info["height"],
                    media_info["duration_seconds"],
                    media_info["has_audio"],
                    media_info["file_size"],
                )
            except (FFprobeNotFoundError, InvalidVideoFileError) as exc:
                error = f"Kiểm tra file video thất bại: {exc}"
                if isinstance(exc, InvalidVideoFileError):
                    try:
                        os.remove(video_path)
                    except OSError:
                        pass
                    self.job_store.set_download_path(self.profile_id, video.aweme_id, "")
                self.job_store.set_status(
                    self.profile_id,
                    video.aweme_id,
                    "failed_download",
                    error=error,
                )
                send_error_notification(
                    error,
                    profile_id=self.profile_id,
                    video_id=video.aweme_id,
                )
                return {"status": "failed_download", "results": {}, "error": error}

            successful_platforms = self.job_store.successful_platforms(
                self.profile_id, video.aweme_id
            )
            self.job_store.set_status(self.profile_id, video.aweme_id, "uploading")

            def record_platform_status(platform: str, status: str, error: str) -> None:
                self.job_store.set_platform_status(
                    self.profile_id,
                    video.aweme_id,
                    platform,
                    status,
                    error=error,
                )

            results = self.pipeline.run(
                video_path,
                video,
                profile_for_upload,
                successful_platforms=successful_platforms,
                on_platform_status=record_platform_status,
                priority=work_priority,
                cancel_event=stop_event,
            )
            finished_job = self.job_store.get_job(self.profile_id, video.aweme_id) or {}
            send_video_upload_summary_notification(
                self.profile_id,
                self.name,
                self._source_label(source),
                video,
                enabled_platform_names(profile_for_upload),
                results,
                platform_states=finished_job.get("platforms", {}),
            )

            if all_enabled_uploads_succeeded(results, profile_for_upload):
                self.job_store.set_status(self.profile_id, video.aweme_id, "completed")
                monitor.mark_processed(video)
                try:
                    if os.path.isfile(video_path):
                        os.remove(video_path)
                    self.job_store.set_download_path(self.profile_id, video.aweme_id, "")
                except OSError as exc:
                    logger.debug("Không thể xóa file video đã hoàn tất %s: %s", video_path, exc)
                return {"status": "completed", "results": results}

            failed_platforms = [
                platform
                for platform in enabled_platform_names(profile_for_upload)
                if not results.get(platform, False)
            ]
            error = "Đăng thất bại: " + ", ".join(failed_platforms)
            self.job_store.set_status(
                self.profile_id,
                video.aweme_id,
                "failed_upload",
                error=error,
            )
            send_error_notification(
                error,
                profile_id=self.profile_id,
                video_id=video.aweme_id,
            )
            return {"status": "failed_upload", "results": results, "error": error}
        except WorkloadCancelled:
            current = self.job_store.get_job(self.profile_id, video.aweme_id) or {}
            next_status = "downloaded" if current.get("download_path") else "caption_ready"
            self.job_store.set_status(self.profile_id, video.aweme_id, next_status)
            logger.info(
                "[Profile %s] Dừng chờ tài nguyên cho video %s.",
                self.profile_id,
                video.aweme_id,
            )
            return {"status": "stopped", "results": {}}
        except Exception as exc:
            error = str(exc)
            logger.exception(
                "[Profile %s] Lỗi xử lý video %s: %s",
                self.profile_id,
                video.aweme_id,
                exc,
            )
            try:
                self.job_store.set_status(
                    self.profile_id,
                    video.aweme_id,
                    "failed",
                    error=error,
                )
            except Exception:
                pass
            send_error_notification(
                f"Lỗi xử lý video: {error}",
                profile_id=self.profile_id,
                video_id=video.aweme_id,
            )
            return {"status": "failed", "results": {}, "error": error}
        finally:
            self.job_store.release(self.profile_id, video.aweme_id)

    def run_source(self, source: dict):
        sec_uid = source["target_sec_uid"]
        source_label = self._source_label(source)
        monitor = self._create_monitor(source)

        console.print(
            f"\n[bold yellow]🔄 [Profile {self.profile_id} - {self.name}] "
            f"Quét nguồn {source_label}...[/]"
        )
        try:
            pending_videos = self.get_pending_videos(source)
            pending_ids = {video.aweme_id for video in pending_videos}
            detected_videos = run_with_gemlogin_recovery(
                monitor.get_new_videos,
                self.gemlogin_id,
                config.API_URL,
                operation_name=f"Quét nguồn {source_label}",
                attempts=2,
            )
            new_videos = [
                video for video in detected_videos if video.aweme_id not in pending_ids
            ]

            if not pending_videos and not new_videos:
                console.print(f"[dim]   → Nguồn {source_label} không có video mới.[/]")
                return

            if len(new_videos) > MAX_NEW_VIDEOS_PER_SOURCE:
                newest_video = max(new_videos, key=lambda video: video.create_time)
                ignored_videos = [
                    video for video in new_videos if video.aweme_id != newest_video.aweme_id
                ]
                for ignored_video in ignored_videos:
                    self.mark_ignored_job(
                        ignored_video,
                        source,
                        monitor,
                        "Bỏ qua vì trong một lượt quét có nhiều video mới hơn giới hạn.",
                    )
                console.print(
                    f"[yellow]   → Có {len(new_videos)} video mới; bỏ qua "
                    f"{len(ignored_videos)} video cũ hơn và chỉ xử lý video mới nhất.[/]"
                )
                new_videos = [newest_video]

            for video in new_videos:
                send_new_video_notification(
                    self.profile_id,
                    self.name,
                    source_label,
                    video,
                    enabled_platform_names(self.profile),
                )
                self.register_video(video, source)

            queue = pending_videos + new_videos
            for video in queue:
                console.print(
                    f"[bold cyan]🎬 [{source_label}] Xử lý video mới: {video.aweme_id} "
                    f"({video.like_count} likes, {video.duration_seconds:.1f}s)[/]"
                )
                outcome = self.process_video(video, source, monitor)
                if outcome.get("status") == "completed":
                    console.print(
                        f"[bold green]✅ [{source_label}] Đã đăng và lưu trạng thái "
                        f"video {video.aweme_id}![/]"
                    )
                elif outcome.get("status") == "cancelled":
                    console.print(
                        f"[yellow]   → Đã hủy video {video.aweme_id} theo yêu cầu từ Telegram.[/]"
                    )
                elif outcome.get("status") != "busy":
                    logger.error(
                        f"[Profile {self.profile_id}] Video {video.aweme_id} chưa hoàn tất: "
                        f"{outcome.get('status')} - {outcome.get('error', '')}"
                    )
        except Exception as e:
            logger.error(
                f"[Profile {self.profile_id}] Lỗi khi quét nguồn {source_label}: {e}"
            )
            send_error_notification(
                f"Lỗi khi quét nguồn {source_label}: {e}",
                profile_id=self.profile_id,
            )

    def run_once(self):
        if not self.sources:
            logger.warning(f"[Profile {self.profile_id}] Không có nguồn Douyin nào đang bật.")
            return
        for source in self.sources:
            self.run_source(source)
