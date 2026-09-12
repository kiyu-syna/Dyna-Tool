from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import socket
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.playwright_runtime import configure_packaged_playwright_driver

configure_packaged_playwright_driver()

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import core.config as config
from core.runtime_paths import logs_dir
import services.account.auth_service as auth_service
import services.account.license_service as license_service
from application.tracking.sources import (
    get_tracking_sources,
    tracking_source_label,
)
from application.workflows.video_job_store import VideoJobStore
from services.runtime.overview_service import build_overview_snapshot
from services.browser.browser_runtime_service import (
    BrowserRuntimeError,
    install_browser_runtime,
    install_playwright_chromium_runtime,
    list_browser_runtimes,
)
from application.workflows.local_profile_setup_service import (
    LocalProfileSetupError,
    LocalProfileSetupService,
)
from services.runtime.log_view_service import build_log_snapshot, reset_log_files
from application.tracking.profile_diagnostics_service import run_profile_diagnostics
from application.tracking.profile_management_service import ProfileManagementService
from application.publishing.profile_test_upload_service import ProfileTestUploadService
from services.runtime.tracking_runtime_service import TrackingRuntimeService
from application.publishing.extension_upload_service import ExtensionUploadRequest, ExtensionUploadService
from application.publishing.manual_publish_service import (
    ManualPublishItem,
    ManualPublishRequest,
    ManualPublishService,
    ManualPublishTarget,
)
from application.publishing.douyin_selection_service import DouyinSelectionService
from services.assistant.ai_assistant_service import AiAssistantError, AiAssistantService
from services.video_ai.video_ai_service import VideoAiService
from application.workflows.telegram_remote_action_worker import TelegramRemoteActionWorker


APP_VERSION = "0.1.0"
DYNA_EXTENSION_ID = "ibdfeimkglcmdejppabkaidpippniiob"
DYNA_EXTENSION_ORIGIN = f"chrome-extension://{DYNA_EXTENSION_ID}"
LICENSE_PLANS = [
    {"days": 7, "label": "7 ngày", "price": 99000, "per_day": "~14k/ngày"},
    {"days": 14, "label": "14 ngày", "price": 169000, "per_day": "~12k/ngày"},
    {"days": 30, "label": "30 ngày", "price": 249000, "per_day": "~8k/ngày", "tag": "Phổ biến"},
    {"days": 60, "label": "60 ngày", "price": 449000, "per_day": "~7.5k/ngày"},
    {"days": 90, "label": "90 ngày", "price": 599000, "per_day": "~6.7k/ngày"},
    {"days": 365, "label": "1 năm", "price": 1799000, "per_day": "~4.9k/ngày", "tag": "Tiết kiệm 41%"},
]
PROFILE_DIR = Path(config.BASE_DIR) / "profile_automation" / "profiles"
LOG_FILE = logs_dir() / "system.log"
SETTINGS_FILE = Path(config.SETTINGS_FILE)
SETTINGS_KEYS = {
    "API_URL",
    "PAYMENT_API_URL",
    "MAX_CONCURRENT_DOWNLOADS",
    "MAX_CONCURRENT_FFMPEG",
    "MAX_CONCURRENT_UPLOADS",
    "TELEGRAM_NOTIFICATION_TYPES",
    "UI_THEME",
    "UI_LANGUAGE",
}
CONCURRENCY_SETTING_KEYS = {
    "MAX_CONCURRENT_DOWNLOADS",
    "MAX_CONCURRENT_FFMPEG",
    "MAX_CONCURRENT_UPLOADS",
}
TELEGRAM_NOTIFICATION_TYPES = {
    "new_video",
    "upload_success",
    "upload_failure",
    "high_ram",
    "job_confirmation",
}


class ApiContext:
    def __init__(
        self,
        token: str,
        job_store: VideoJobStore | None = None,
        runtime: TrackingRuntimeService | None = None,
        profiles: ProfileManagementService | None = None,
        test_uploads: ProfileTestUploadService | None = None,
        extension_uploads: ExtensionUploadService | None = None,
        manual_publish: ManualPublishService | None = None,
        douyin_selections: DouyinSelectionService | None = None,
        local_profiles: LocalProfileSetupService | None = None,
        assistant: AiAssistantService | None = None,
        video_ai: VideoAiService | None = None,
    ):
        self.token = token
        self.jobs = job_store or VideoJobStore()
        self.runtime = runtime or TrackingRuntimeService()
        self.profiles = profiles or ProfileManagementService(PROFILE_DIR)
        self.test_uploads = test_uploads or ProfileTestUploadService(self.profiles)
        self.extension_uploads = extension_uploads or ExtensionUploadService(
            self.jobs,
            self.profiles,
        )
        self.manual_publish = manual_publish or ManualPublishService(self.extension_uploads)
        self.douyin_selections = douyin_selections or DouyinSelectionService(
            self.manual_publish
        )
        self.local_profiles = local_profiles or LocalProfileSetupService(self.profiles)
        self.assistant = assistant or AiAssistantService()
        self.video_ai = video_ai or VideoAiService(self.assistant)
        self.telegram_remote_actions = TelegramRemoteActionWorker(self.runtime, self.manual_publish, self.jobs)

    def start_background_services(self) -> None:
        from services.runtime.runtime_maintenance_service import run_runtime_maintenance
        resumed_imported_jobs = self.extension_uploads.resume_pending_jobs()
        self.manual_publish.start_scheduler()
        self.telegram_remote_actions.start()
        if resumed_imported_jobs:
            from core.utils import logger

            logger.info(
                "[Đăng video nội bộ] Đã khôi phục %s tác vụ sau khi Dyna khởi động.",
                resumed_imported_jobs,
            )
        threading.Thread(
            target=run_runtime_maintenance,
            daemon=True,
            name="desktop-runtime-maintenance",
        ).start()


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _apply_runtime_settings(settings: dict[str, Any]) -> None:
    """Keep modules that still read config globals in sync after a desktop save."""
    config.settings = settings
    config.API_URL = str(settings.get("API_URL") or "http://127.0.0.1:1010")


def _profile_summary(
    profile_id: str,
    profile: dict[str, Any],
    jobs: list[dict],
    active_profile_ids: set[str] | None = None,
) -> dict:
    sources = get_tracking_sources(profile, include_disabled=True)
    profile_jobs = [job for job in jobs if str(job.get("profile_id")) == profile_id]
    active_job = next((job for job in profile_jobs if job.get("active")), None)
    return {
        "id": profile_id,
        "name": str(profile.get("name") or f"Profile {profile_id}"),
        "enabled": bool(profile.get("enabled", True)),
        "source_count": len(sources),
        "enabled_source_count": sum(bool(source.get("enabled", True)) for source in sources),
        "source_platforms": {
            platform: sum(source.get("platform") == platform for source in sources)
            for platform in ("douyin", "tiktok")
        },
        "check_interval_minutes": int(profile.get("check_interval_minutes") or 30),
        "platforms": {
            platform: bool((profile.get(platform, {}) or {}).get("enabled", False))
            for platform in ("tiktok", "youtube", "facebook")
        },
        "running": active_job is not None or profile_id in (active_profile_ids or set()),
        "current_video": str((active_job or {}).get("video_id") or ""),
        "queue_count": sum(
            job.get("status") not in {"completed", "cancelled", "ignored"}
            for job in profile_jobs
        ),
        "error_count": sum(
            str(job.get("status") or "").startswith("failed") for job in profile_jobs
        ),
    }


def _apply_job_source_display_names(rows: list[dict[str, Any]]) -> None:
    """Use the current Profile source names instead of stored Douyin identifiers."""
    profiles = config.load_profile_configs()
    source_maps: dict[str, dict[str, str]] = {}
    for profile_id, profile in profiles.items():
        labels: dict[str, str] = {}
        for source in get_tracking_sources(profile, include_disabled=True):
            display_name = tracking_source_label(source)
            labels[str(source.get("source_key") or "")] = display_name
            labels[str(source.get("sec_uid") or source.get("unique_id") or "")] = display_name
        source_maps[str(profile_id)] = labels

    for row in rows:
        labels = source_maps.get(str(row.get("profile_id") or ""), {})
        display_name = labels.get(str(row.get("source_key") or ""))
        if not display_name:
            display_name = labels.get(str(row.get("source_label") or ""))
        if display_name:
            row["source_label"] = display_name


def create_app(
    token: str,
    *,
    job_store: VideoJobStore | None = None,
    runtime: TrackingRuntimeService | None = None,
    profile_service: ProfileManagementService | None = None,
    test_upload_service: ProfileTestUploadService | None = None,
    extension_upload_service: ExtensionUploadService | None = None,
    manual_publish_service: ManualPublishService | None = None,
    douyin_selection_service: DouyinSelectionService | None = None,
    local_profile_setup_service: LocalProfileSetupService | None = None,
    ai_assistant_service: AiAssistantService | None = None,
    video_ai_service: VideoAiService | None = None,
    start_background_services: bool = False,
) -> FastAPI:
    context = ApiContext(
        token,
        job_store=job_store,
        runtime=runtime,
        profiles=profile_service,
        test_uploads=test_upload_service,
        extension_uploads=extension_upload_service,
        manual_publish=manual_publish_service,
        douyin_selections=douyin_selection_service,
        local_profiles=local_profile_setup_service,
        assistant=ai_assistant_service,
        video_ai=video_ai_service,
    )
    if start_background_services:
        context.start_background_services()
    app = FastAPI(
        title="Dyna Desktop API",
        version=APP_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[DYNA_EXTENSION_ORIGIN],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Dyna-Extension-Id"],
    )

    def authorize(x_dyna_token: str = Header(default="")) -> None:
        if not context.token or x_dyna_token != context.token:
            raise HTTPException(status_code=401, detail="Unauthorized")

    protected = [Depends(authorize)]

    def authorize_extension(
        origin: str = Header(default=""),
        x_dyna_extension_id: str = Header(default=""),
    ) -> None:
        if x_dyna_extension_id != DYNA_EXTENSION_ID:
            raise HTTPException(status_code=401, detail="Dyna extension không hợp lệ.")
        if origin and origin != DYNA_EXTENSION_ORIGIN:
            raise HTTPException(status_code=403, detail="Origin extension không được phép.")

    extension_protected = [Depends(authorize_extension)]

    def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
        if not user:
            return None
        return {
            key: user.get(key)
            for key in ("username", "phone", "display_name")
            if user.get(key) not in (None, "")
        }

    from desktop_backend.routers import (
        account,
        assistant,
        browser,
        extension,
        jobs,
        logs,
        profiles,
        publishing,
        runtime,
        settings,
        system,
        video_ai,
    )

    hooks = sys.modules[__name__]
    extension.register_routes(
        app,
        context,
        extension_protected,
        APP_VERSION=APP_VERSION,
    )
    publishing.register_routes(app, context, protected)
    video_ai.register_routes(app, context, protected)
    system.register_routes(app, protected, APP_VERSION=APP_VERSION)
    assistant.register_routes(
        app,
        context,
        protected,
        _profile_summary=_profile_summary,
    )
    browser.register_routes(app, context, protected, hooks=hooks)
    account.register_routes(
        app,
        context,
        protected,
        LICENSE_PLANS=LICENSE_PLANS,
        public_user=public_user,
    )
    runtime.register_routes(
        app,
        context,
        protected,
        _profile_summary=_profile_summary,
    )
    profiles.register_routes(app, context, protected)
    jobs.register_routes(
        app,
        context,
        protected,
        _apply_job_source_display_names=_apply_job_source_display_names,
    )
    settings.register_routes(
        app,
        protected,
        hooks=hooks,
        SETTINGS_KEYS=SETTINGS_KEYS,
        TELEGRAM_NOTIFICATION_TYPES=TELEGRAM_NOTIFICATION_TYPES,
        CONCURRENCY_SETTING_KEYS=CONCURRENCY_SETTING_KEYS,
    )
    logs.register_routes(app, context, protected, hooks=hooks)
    return app


def _bound_socket(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    sock.bind((host, port))
    return sock


class _ReadyServer(uvicorn.Server):
    def __init__(self, server_config: uvicorn.Config, ready_line: str):
        super().__init__(server_config)
        self._ready_line = ready_line

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started:
            print(self._ready_line, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dyna Tool Electron backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", default=os.environ.get("DYNA_DESKTOP_TOKEN", ""))
    args = parser.parse_args()
    if not args.token:
        raise SystemExit("Missing desktop API token")

    sock = _bound_socket(args.host, args.port)
    actual_port = int(sock.getsockname()[1])
    ready = json.dumps(
        {"host": args.host, "port": actual_port, "pid": os.getpid()},
        ensure_ascii=True,
    )
    _ReadyServer(
        uvicorn.Config(
            create_app(args.token, start_background_services=True),
            host=args.host,
            port=actual_port,
            log_level="warning",
            access_log=False,
        ),
        f"DYNA_API_READY {ready}",
    ).run(sockets=[sock])


if __name__ == "__main__":
    main()
