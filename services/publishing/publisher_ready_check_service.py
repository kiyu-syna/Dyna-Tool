from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import quote

import core.config as config
from profile_automation.browser_utils import is_captcha_present
from core.utils import logger
from services.browser.browser_profile_service import (
    browser_provider,
    connected_browser_profile,
    create_background_page,
)
from services.profiles.profile_management_service import ProfileManagementService


SUPPORTED_PLATFORMS = ("tiktok", "youtube", "facebook")
READY_TTL = timedelta(minutes=15)
NOT_READY_TTL = timedelta(minutes=2)
PAGE_CHECK_TIMEOUT_SECONDS = 18


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _close_page_quietly(page) -> None:
    if page is None:
        return
    try:
        if not page.is_closed():
            page.close()
    except Exception:
        pass


class PublisherReadyCheckService:
    """Checks that selected publishing accounts can reach their upload composer."""

    def __init__(self, profiles: ProfileManagementService) -> None:
        self.profiles = profiles
        self._lock = threading.RLock()
        self._states: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, set[str]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._execution_locks: dict[str, threading.RLock] = {}
        self._stopping = threading.Event()

    @staticmethod
    def _key(profile_id: str, platform: str) -> str:
        return f"{profile_id}:{platform}"

    def _execution_lock(self, profile_id: str) -> threading.RLock:
        with self._lock:
            return self._execution_locks.setdefault(profile_id, threading.RLock())

    @staticmethod
    def _unknown_state(profile_id: str, platform: str) -> dict[str, Any]:
        return {
            "profile_id": profile_id,
            "platform": platform,
            "status": "unknown",
            "ready": False,
            "message": "Chưa kiểm tra.",
            "checked_at": "",
            "expires_at": "",
        }

    def _state(self, profile_id: str, platform: str) -> dict[str, Any]:
        with self._lock:
            return dict(
                self._states.get(self._key(profile_id, platform))
                or self._unknown_state(profile_id, platform)
            )

    @staticmethod
    def _is_fresh(state: dict[str, Any], now: datetime | None = None) -> bool:
        if str(state.get("status") or "") in {"unknown", "checking"}:
            return False
        expires_at = _parse_iso(state.get("expires_at"))
        return bool(expires_at and expires_at > (now or _utc_now()))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            checks = [dict(row) for row in self._states.values()]
            checking_profiles = sorted(
                profile_id
                for profile_id, thread in self._threads.items()
                if thread.is_alive()
            )
        checks.sort(key=lambda row: (str(row.get("profile_id") or "").zfill(8), str(row.get("platform") or "")))
        return {
            "checks": checks,
            "checking": bool(checking_profiles),
            "checking_profiles": checking_profiles,
        }

    def profile_states(self, profile_id: str, platforms: Iterable[str]) -> dict[str, dict[str, Any]]:
        normalized_id = self.profiles.normalize_profile_id(profile_id)
        return {
            platform: self._state(normalized_id, platform)
            for platform in self._normalize_platforms(platforms)
        }

    @staticmethod
    def _normalize_platforms(platforms: Iterable[str]) -> tuple[str, ...]:
        normalized = tuple(
            dict.fromkeys(
                str(platform).strip().lower()
                for platform in platforms
                if str(platform).strip()
            )
        )
        unknown = [platform for platform in normalized if platform not in SUPPORTED_PLATFORMS]
        if unknown:
            raise ValueError(f"Nền tảng không được hỗ trợ: {', '.join(unknown)}")
        if not normalized:
            raise ValueError("Hãy chọn ít nhất một nền tảng để kiểm tra sẵn sàng.")
        return normalized

    def _validate_request(self, profile_id: str, platforms: Iterable[str]) -> tuple[str, dict[str, Any], tuple[str, ...]]:
        normalized_id = self.profiles.normalize_profile_id(profile_id)
        normalized_platforms = self._normalize_platforms(platforms)
        profile = self.profiles.load(normalized_id)
        unavailable = [
            platform
            for platform in normalized_platforms
            if not bool((profile.get(platform) or {}).get("enabled", False))
        ]
        if unavailable:
            raise ValueError(
                f"Profile {normalized_id} chưa bật nền tảng: {', '.join(unavailable)}"
            )
        return normalized_id, profile, normalized_platforms

    def start_checks(
        self,
        targets: Iterable[tuple[str, Iterable[str]]],
        *,
        force: bool = True,
    ) -> dict[str, Any]:
        requested: list[tuple[str, tuple[str, ...]]] = []
        for profile_id, platforms in targets:
            normalized_id, _profile, normalized_platforms = self._validate_request(profile_id, platforms)
            requested.append((normalized_id, normalized_platforms))

        self._stopping.clear()
        for profile_id, platforms in requested:
            due = tuple(
                platform
                for platform in platforms
                if force or not self._is_fresh(self._state(profile_id, platform))
            )
            if not due:
                continue
            with self._lock:
                self._pending.setdefault(profile_id, set()).update(due)
                for platform in due:
                    previous = self._states.get(self._key(profile_id, platform), {})
                    self._states[self._key(profile_id, platform)] = {
                        **self._unknown_state(profile_id, platform),
                        "status": "checking",
                        "message": "Đang mở trang đăng để kiểm tra phiên...",
                        "checked_at": str(previous.get("checked_at") or ""),
                        "expires_at": "",
                    }
                running = self._threads.get(profile_id)
                if running and running.is_alive():
                    continue
                thread = threading.Thread(
                    target=self._profile_check_loop,
                    args=(profile_id,),
                    daemon=True,
                    name=f"publisher-ready-check-{profile_id}",
                )
                self._threads[profile_id] = thread
                thread.start()
        return self.snapshot()

    def _profile_check_loop(self, profile_id: str) -> None:
        try:
            while not self._stopping.is_set():
                with self._lock:
                    platforms = tuple(sorted(self._pending.pop(profile_id, set())))
                if not platforms:
                    return
                try:
                    self.ensure_ready(profile_id, platforms, force=True)
                except Exception as exc:
                    logger.exception("[Kiểm tra sẵn sàng] Profile %s thất bại: %s", profile_id, exc)
                    self._store_results(
                        profile_id,
                        {
                            platform: {
                                "status": "error",
                                "ready": False,
                                "message": f"Không thể kiểm tra: {exc}",
                            }
                            for platform in platforms
                        },
                    )
        finally:
            with self._lock:
                current = self._threads.get(profile_id)
                if current is threading.current_thread():
                    self._threads.pop(profile_id, None)

    def ensure_ready(
        self,
        profile_id: str,
        platforms: Iterable[str],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        normalized_id, profile, normalized_platforms = self._validate_request(profile_id, platforms)
        execution_lock = self._execution_lock(normalized_id)
        with execution_lock:
            due = tuple(
                platform
                for platform in normalized_platforms
                if force or not self._is_fresh(self._state(normalized_id, platform))
            )
            if due:
                results = self._check_profile_platforms(normalized_id, profile, due)
                self._store_results(normalized_id, results)

        checks = [self._state(normalized_id, platform) for platform in normalized_platforms]
        ready = all(bool(check.get("ready")) for check in checks)
        messages = [str(check.get("message") or "") for check in checks if not check.get("ready")]
        return {
            "profile_id": normalized_id,
            "ready": ready,
            "checks": checks,
            "message": "Sẵn sàng đăng." if ready else "; ".join(messages),
        }

    def _store_results(self, profile_id: str, results: dict[str, dict[str, Any]]) -> None:
        now = _utc_now()
        with self._lock:
            for platform, result in results.items():
                ready = bool(result.get("ready"))
                ttl = READY_TTL if ready else NOT_READY_TTL
                self._states[self._key(profile_id, platform)] = {
                    "profile_id": profile_id,
                    "platform": platform,
                    "status": str(result.get("status") or ("ready" if ready else "error")),
                    "ready": ready,
                    "message": str(result.get("message") or ""),
                    "checked_at": _iso(now),
                    "expires_at": _iso(now + ttl),
                }

    @staticmethod
    def _browser_id(profile_id: str, profile: dict[str, Any], platform: str) -> str:
        if browser_provider(profile) == "local_chromium":
            return "__local_chromium__"
        return str((profile.get(platform) or {}).get("gemlogin_profile_id") or profile_id).strip()

    def _check_profile_platforms(
        self,
        profile_id: str,
        profile: dict[str, Any],
        platforms: tuple[str, ...],
    ) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[str]] = {}
        for platform in platforms:
            grouped.setdefault(self._browser_id(profile_id, profile, platform), []).append(platform)

        results: dict[str, dict[str, Any]] = {}
        for browser_key, browser_platforms in grouped.items():
            browser_profile_id = profile_id if browser_key == "__local_chromium__" else browser_key
            try:
                with connected_browser_profile(
                    browser_profile_id,
                    config.API_URL,
                    profile_config=profile,
                    attempts=2,
                    retry_delay_seconds=0.5,
                ) as browser:
                    if not browser.contexts:
                        raise RuntimeError("Trình duyệt không có context.")
                    context = browser.contexts[0]
                    for platform in browser_platforms:
                        page = None
                        try:
                            page = create_background_page(browser, context)
                            results[platform] = self._inspect_platform_page(page, platform, profile)
                        except Exception as exc:
                            results[platform] = {
                                "status": "error",
                                "ready": False,
                                "message": f"Không mở được trang {platform}: {exc}",
                            }
                        finally:
                            _close_page_quietly(page)
            except Exception as exc:
                for platform in browser_platforms:
                    results[platform] = {
                        "status": "error",
                        "ready": False,
                        "message": f"Không kết nối được trình duyệt: {exc}",
                    }
        return results

    @staticmethod
    def _platform_url(platform: str, profile: dict[str, Any]) -> str:
        if platform == "tiktok":
            return "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
        if platform == "youtube":
            channel_id = str((profile.get("youtube") or {}).get("channel_id") or "").strip()
            if not channel_id:
                return ""
            return f"https://studio.youtube.com/channel/{quote(channel_id, safe='')}/videos/upload?d=ud"
        return str((profile.get("facebook") or {}).get("profile_url") or "https://www.facebook.com/me").strip()

    @staticmethod
    def _visible(page, selectors: tuple[str, ...]) -> bool:
        for selector in selectors:
            try:
                matches = page.locator(selector)
                for index in range(min(matches.count(), 5)):
                    if matches.nth(index).is_visible():
                        return True
            except Exception:
                continue
        return False

    @staticmethod
    def _exists(page, selectors: tuple[str, ...]) -> bool:
        for selector in selectors:
            try:
                if page.locator(selector).count() > 0:
                    return True
            except Exception:
                continue
        return False

    def _inspect_platform_page(self, page, platform: str, profile: dict[str, Any]) -> dict[str, Any]:
        url = self._platform_url(platform, profile)
        if not url:
            return {
                "status": "attention",
                "ready": False,
                "message": "YouTube chưa có Channel ID.",
            }

        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        deadline = time.monotonic() + PAGE_CHECK_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            current_url = str(page.url or "").casefold()
            try:
                captcha = is_captcha_present(page)
            except Exception:
                captcha = False
            if captcha:
                return {
                    "status": "attention",
                    "ready": False,
                    "message": "Đang có CAPTCHA; hãy mở Profile và xử lý.",
                }

            if platform == "tiktok":
                if "login" in current_url or "passport.tiktok" in current_url:
                    return {"status": "login_required", "ready": False, "message": "TikTok đã hết phiên đăng nhập."}
                if self._visible(
                    page,
                    (
                        'button:has-text("Select video")',
                        'button:has-text("Chọn video")',
                        'div.Button__content:has-text("Select video")',
                    ),
                ) or self._exists(page, ('input[type="file"][accept*="video"]',)):
                    return {"status": "ready", "ready": True, "message": "TikTok Studio sẵn sàng."}
            elif platform == "youtube":
                if "accounts.google.com" in current_url or "/signin" in current_url:
                    return {"status": "login_required", "ready": False, "message": "YouTube đã hết phiên đăng nhập."}
                if self._visible(
                    page,
                    (
                        'button[aria-label="Chọn tệp"]',
                        'button[aria-label="Select files"]',
                    ),
                ):
                    return {"status": "ready", "ready": True, "message": "YouTube Studio sẵn sàng."}
            else:
                if any(marker in current_url for marker in ("/login", "/checkpoint", "/recover")) or self._visible(
                    page,
                    ('input[name="email"]', 'input[name="pass"]'),
                ):
                    return {"status": "login_required", "ready": False, "message": "Facebook đã hết phiên đăng nhập hoặc đang yêu cầu xác minh."}
                if self._visible(
                    page,
                    (
                        '[role="button"][aria-label="Ảnh/video"]',
                        '[role="button"][aria-label="Photo/video"]',
                    ),
                ):
                    return {"status": "ready", "ready": True, "message": "Facebook Reels sẵn sàng."}
            time.sleep(0.5)

        labels = {"tiktok": "TikTok Studio", "youtube": "YouTube Studio", "facebook": "Facebook"}
        return {
            "status": "attention",
            "ready": False,
            "message": f"{labels[platform]} đã mở nhưng không thấy khu vực đăng; hãy kiểm tra tài khoản.",
        }

    def stop(self) -> None:
        self._stopping.set()
