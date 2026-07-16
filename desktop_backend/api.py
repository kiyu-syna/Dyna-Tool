from __future__ import annotations

import argparse
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

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import core.config as config
import services.auth_service as auth_service
import services.busy_mode_service as busy_mode_service
import services.license_service as license_service
from profile_automation.douyin_sources import get_douyin_sources
from profile_automation.pipeline.video_job_store import VideoJobStore
from services.overview_service import build_overview_snapshot
from services.profile_diagnostics_service import run_profile_diagnostics
from services.profile_management_service import ProfileManagementService
from services.profile_test_upload_service import ProfileTestUploadService
from services.tracking_runtime_service import TrackingRuntimeService
from services.extension_upload_service import ExtensionUploadRequest, ExtensionUploadService


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
LOG_FILE = Path(config.BASE_DIR) / "logs" / "system.log"
SETTINGS_FILE = Path(config.SETTINGS_FILE)
SETTINGS_KEYS = {
    "API_URL",
    "PAYMENT_API_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_QUIET_HOURS_ENABLED",
    "TELEGRAM_QUIET_START",
    "TELEGRAM_QUIET_END",
    "MAX_CONCURRENT_DOWNLOADS",
    "MAX_CONCURRENT_FFMPEG",
    "MAX_CONCURRENT_UPLOADS",
    "UI_THEME",
    "UI_LANGUAGE",
}
CONCURRENCY_SETTING_KEYS = {
    "MAX_CONCURRENT_DOWNLOADS",
    "MAX_CONCURRENT_FFMPEG",
    "MAX_CONCURRENT_UPLOADS",
}


class ProfilePayload(BaseModel):
    profile: dict[str, Any]


class CreateProfilePayload(BaseModel):
    id: str = Field(min_length=1)
    name: str = ""


class SeenStatePayload(BaseModel):
    data: dict[str, Any]


class DiagnosticPayload(BaseModel):
    profile_id: str | None = None


class LoginPayload(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RegisterPayload(BaseModel):
    phone: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password: str = Field(min_length=6)


class CreateOrderPayload(BaseModel):
    days: int


class TestUploadPayload(BaseModel):
    confirmed: bool = False


class SettingsPayload(BaseModel):
    settings: dict[str, Any]


class BusyModePayload(BaseModel):
    busy: bool


class JobActionPayload(BaseModel):
    profile_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)


class ExtensionJobPayload(BaseModel):
    profile_id: str = Field(min_length=1, pattern=r"^\d+$")
    video_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    file_path: str = Field(min_length=1, max_length=4096)
    source_url: str = Field(default="", max_length=4096)
    description: str = Field(default="", max_length=10000)
    create_time: int = 0
    duration_ms: int = 0
    like_count: int = 0
    play_count: int = 0
    author_uid: str = Field(default="", max_length=256)
    author_nickname: str = Field(default="", max_length=512)
    download_url: str = Field(default="", max_length=8192)
    download_id: int = 0


class ApiContext:
    def __init__(
        self,
        token: str,
        job_store: VideoJobStore | None = None,
        runtime: TrackingRuntimeService | None = None,
        profiles: ProfileManagementService | None = None,
        test_uploads: ProfileTestUploadService | None = None,
        extension_uploads: ExtensionUploadService | None = None,
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

    def start_background_services(self) -> None:
        from services.runtime_maintenance_service import run_runtime_maintenance
        from services.telegram_service import ensure_telegram_listener_running

        ensure_telegram_listener_running()
        resumed_extension_jobs = self.extension_uploads.resume_pending_jobs()
        if resumed_extension_jobs:
            from core.utils import logger

            logger.info(
                "[Extension] Resumed %s browser job(s) after Dyna startup.",
                resumed_extension_jobs,
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
    config.TELEGRAM_BOT_TOKEN = str(settings.get("TELEGRAM_BOT_TOKEN") or "")
    config.TELEGRAM_CHAT_ID = str(settings.get("TELEGRAM_CHAT_ID") or "")
    config.TELEGRAM_QUIET_HOURS_ENABLED = bool(
        settings.get("TELEGRAM_QUIET_HOURS_ENABLED", False)
    )
    config.TELEGRAM_QUIET_START = str(settings.get("TELEGRAM_QUIET_START") or "22:00")
    config.TELEGRAM_QUIET_END = str(settings.get("TELEGRAM_QUIET_END") or "07:00")


def _profile_summary(
    profile_id: str,
    profile: dict[str, Any],
    jobs: list[dict],
    active_profile_ids: set[str] | None = None,
) -> dict:
    sources = get_douyin_sources(profile, include_disabled=True)
    profile_jobs = [job for job in jobs if str(job.get("profile_id")) == profile_id]
    active_job = next((job for job in profile_jobs if job.get("active")), None)
    return {
        "id": profile_id,
        "name": str(profile.get("name") or f"Profile {profile_id}"),
        "enabled": bool(profile.get("enabled", True)),
        "source_count": len(sources),
        "enabled_source_count": sum(bool(source.get("enabled", True)) for source in sources),
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


def create_app(
    token: str,
    *,
    job_store: VideoJobStore | None = None,
    runtime: TrackingRuntimeService | None = None,
    profile_service: ProfileManagementService | None = None,
    test_upload_service: ProfileTestUploadService | None = None,
    extension_upload_service: ExtensionUploadService | None = None,
    start_background_services: bool = False,
) -> FastAPI:
    context = ApiContext(
        token,
        job_store=job_store,
        runtime=runtime,
        profiles=profile_service,
        test_uploads=test_upload_service,
        extension_uploads=extension_upload_service,
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

    @app.get("/api/extension/health", dependencies=extension_protected)
    def extension_health() -> dict:
        return {
            "ok": True,
            "service": "Dyna Extension Bridge",
            "version": APP_VERSION,
            "time": datetime.now().isoformat(timespec="seconds"),
        }

    @app.get("/api/extension/profiles", dependencies=extension_protected)
    def extension_profiles() -> dict:
        return {"profiles": context.extension_uploads.list_profiles()}

    @app.post(
        "/api/extension/jobs",
        dependencies=extension_protected,
        status_code=202,
    )
    def extension_submit_job(payload: ExtensionJobPayload) -> dict:
        try:
            return context.extension_uploads.submit(
                ExtensionUploadRequest(**payload.model_dump())
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.get(
        "/api/extension/jobs/{profile_id}/{video_id}",
        dependencies=extension_protected,
    )
    def extension_job_status(profile_id: str, video_id: str) -> dict:
        job = context.extension_uploads.get_job(profile_id, video_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy video trong hàng đợi Dyna.")
        return {"job": job}

    @app.get("/api/health", dependencies=protected)
    def health() -> dict:
        return {
            "ok": True,
            "version": APP_VERSION,
            "pid": os.getpid(),
            "time": datetime.now().isoformat(timespec="seconds"),
        }

    @app.get("/api/busy-mode", dependencies=protected)
    def busy_mode() -> dict:
        return {"state": busy_mode_service.get_busy_mode_state()}

    @app.put("/api/busy-mode", dependencies=protected)
    def update_busy_mode(payload: BusyModePayload) -> dict:
        return {
            "ok": True,
            "state": busy_mode_service.set_busy_mode(payload.busy, source="sidebar"),
        }

    @app.get("/api/auth/status", dependencies=protected)
    def auth_status(verify: bool = True) -> dict:
        authenticated = auth_service.verify_session() if verify else auth_service.is_logged_in()
        user = auth_service.get_current_user() if authenticated else None
        return {"authenticated": authenticated, "user": public_user(user)}

    @app.post("/api/auth/login", dependencies=protected)
    def login(payload: LoginPayload) -> dict:
        try:
            user = auth_service.login(payload.username, payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Không kết nối được máy chủ: {exc}")
        return {"ok": True, "user": public_user(user)}

    @app.post("/api/auth/register", dependencies=protected)
    def register(payload: RegisterPayload) -> dict:
        try:
            user = auth_service.register(payload.phone, payload.username, payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Không kết nối được máy chủ: {exc}")
        return {"ok": True, "user": public_user(user)}

    @app.post("/api/auth/logout", dependencies=protected)
    def logout() -> dict:
        context.runtime.shutdown()
        auth_service.logout()
        return {"ok": True}

    @app.get("/api/license", dependencies=protected)
    def license_status(refresh: bool = False) -> dict:
        info = license_service.verify_with_server() if refresh else license_service.get_license_info()
        return {
            "is_active": bool(license_service.is_licensed_local()),
            "info": info or {},
            "plans": LICENSE_PLANS,
        }

    @app.post("/api/license/verify", dependencies=protected)
    def verify_license() -> dict:
        info = license_service.save_license_from_server()
        return {"is_active": bool(info.get("is_active")), "info": info}

    @app.post("/api/license/orders", dependencies=protected)
    def create_license_order(payload: CreateOrderPayload) -> dict:
        allowed_days = {plan["days"] for plan in LICENSE_PLANS}
        if payload.days not in allowed_days:
            raise HTTPException(status_code=400, detail="Gói sử dụng không hợp lệ.")
        try:
            return license_service.create_order(payload.days)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Không thể tạo đơn hàng: {exc}")

    @app.get("/api/license/orders/{order_id}", dependencies=protected)
    def payment_status(order_id: str) -> dict:
        try:
            return license_service.poll_payment_status(order_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Không kiểm tra được thanh toán: {exc}")

    @app.get("/api/overview", dependencies=protected)
    def overview() -> dict:
        jobs = context.jobs.list_jobs()
        active_profile_ids = {
            str(job.get("profile_id")) for job in jobs if job.get("active")
        }
        active_profile_ids.update(context.runtime.active_profile_ids())
        return build_overview_snapshot(active_profile_ids=active_profile_ids)

    @app.get("/api/profiles", dependencies=protected)
    def profiles() -> dict:
        profile_configs = config.load_profile_configs()
        jobs = context.jobs.list_jobs()
        active_profile_ids = context.runtime.active_profile_ids()
        rows = [
            _profile_summary(str(profile_id), profile, jobs, active_profile_ids)
            for profile_id, profile in sorted(
                profile_configs.items(), key=lambda item: str(item[0]).zfill(8)
            )
        ]
        return {"profiles": rows}

    @app.get("/api/runtime", dependencies=protected)
    def runtime_status() -> dict:
        return context.runtime.snapshot()

    @app.get("/api/runtime/test-uploads", dependencies=protected)
    def test_upload_status() -> dict:
        return {"profiles": context.test_uploads.snapshot()}

    @app.post(
        "/api/runtime/profiles/{profile_id}/test-upload",
        dependencies=protected,
        status_code=202,
    )
    def start_test_upload(profile_id: str, payload: TestUploadPayload) -> dict:
        if not payload.confirmed:
            raise HTTPException(
                status_code=400,
                detail="Cần xác nhận trước khi test đăng video thật.",
            )
        if profile_id in context.runtime.active_profile_ids():
            raise HTTPException(
                status_code=409,
                detail="Hãy dừng Profile trước khi test đăng video.",
            )
        try:
            return {"state": context.test_uploads.start(profile_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/api/diagnostics", dependencies=protected)
    def diagnostics(payload: DiagnosticPayload) -> dict:
        result = run_profile_diagnostics(payload.profile_id)
        if payload.profile_id and not result["profiles"]:
            raise HTTPException(status_code=404, detail="Không tìm thấy Profile.")
        return result

    @app.post("/api/runtime/profiles/{profile_id}/start", dependencies=protected)
    def start_runtime_profile(profile_id: str) -> dict:
        try:
            state = context.runtime.start_profile(profile_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "state": state}

    @app.post("/api/runtime/profiles/{profile_id}/stop", dependencies=protected)
    def stop_runtime_profile(profile_id: str) -> dict:
        return {"ok": True, "state": context.runtime.stop_profile(profile_id)}

    @app.post("/api/runtime/start-all", dependencies=protected)
    def start_all_runtime_profiles() -> dict:
        return {"ok": True, **context.runtime.start_all()}

    @app.post("/api/runtime/stop-all", dependencies=protected)
    def stop_all_runtime_profiles() -> dict:
        return {"ok": True, **context.runtime.stop_all()}

    @app.post("/api/runtime/shutdown", dependencies=protected)
    def shutdown_runtime() -> dict:
        return {"ok": True, **context.runtime.shutdown()}

    @app.get("/api/profiles/{profile_id}", dependencies=protected)
    def profile_detail(profile_id: str) -> dict:
        try:
            profile = context.profiles.load(profile_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"profile": profile}

    @app.post("/api/profiles", dependencies=protected)
    def create_profile(payload: CreateProfilePayload) -> dict:
        try:
            profile = context.profiles.create(payload.id, payload.name)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "profile": profile}

    @app.put("/api/profiles/{profile_id}", dependencies=protected)
    def save_profile(profile_id: str, payload: ProfilePayload) -> dict:
        try:
            profile = context.profiles.save(profile_id, payload.profile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "profile": profile}

    @app.delete("/api/profiles/{profile_id}", dependencies=protected)
    def delete_profile(profile_id: str) -> dict:
        if str(profile_id) in context.runtime.active_profile_ids():
            raise HTTPException(status_code=409, detail="Hãy dừng Profile trước khi xóa.")
        try:
            context.profiles.delete(profile_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True}

    @app.get("/api/profiles/{profile_id}/seen", dependencies=protected)
    def seen_sources(profile_id: str) -> dict:
        try:
            return {"sources": context.profiles.list_seen_sources(profile_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/api/profiles/{profile_id}/seen/{source_key}", dependencies=protected)
    def read_seen_state(profile_id: str, source_key: str) -> dict:
        try:
            return context.profiles.read_seen(profile_id, source_key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.put("/api/profiles/{profile_id}/seen/{source_key}", dependencies=protected)
    def save_seen_state(profile_id: str, source_key: str, payload: SeenStatePayload) -> dict:
        if str(profile_id) in context.runtime.active_profile_ids():
            raise HTTPException(
                status_code=409,
                detail="Hãy dừng Profile trước khi sửa dữ liệu đối chiếu.",
            )
        try:
            data = context.profiles.save_seen(profile_id, source_key, payload.data)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "data": data}

    @app.get("/api/jobs", dependencies=protected)
    def jobs(
        profile_id: str | None = None,
        include_terminal: bool = True,
        limit: int = Query(default=150, ge=1, le=500),
    ) -> dict:
        rows = context.jobs.list_jobs(
            profile_id=str(profile_id) if profile_id else None,
            include_terminal=include_terminal,
        )
        rows.sort(key=lambda job: str(job.get("updated_at") or ""), reverse=True)
        return {"jobs": rows[:limit], "total": len(rows)}

    @app.post("/api/jobs/retry", dependencies=protected)
    def retry_job(payload: JobActionPayload) -> dict:
        job = context.jobs.retry_job(payload.profile_id, payload.video_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Video job not found")
        return {"ok": True, "job": job}

    @app.post("/api/jobs/cancel", dependencies=protected)
    def cancel_job(payload: JobActionPayload) -> dict:
        job = context.jobs.cancel_job(
            payload.profile_id,
            payload.video_id,
            reason="Người dùng hủy video từ giao diện desktop.",
        )
        if job is None:
            raise HTTPException(status_code=404, detail="Video job not found")
        return {"ok": True, "job": job}

    @app.get("/api/settings", dependencies=protected)
    def settings() -> dict:
        current = config.load_settings()
        return {"settings": {key: current.get(key) for key in sorted(SETTINGS_KEYS)}}

    @app.put("/api/settings", dependencies=protected)
    def save_settings(payload: SettingsPayload) -> dict:
        current = config.load_settings()
        for key, value in payload.settings.items():
            if key in SETTINGS_KEYS:
                if key in CONCURRENCY_SETTING_KEYS:
                    try:
                        value = int(value)
                    except (TypeError, ValueError) as exc:
                        raise HTTPException(
                            status_code=400,
                            detail=f"{key} phải là số nguyên.",
                        ) from exc
                    if not 1 <= value <= 32:
                        raise HTTPException(
                            status_code=400,
                            detail=f"{key} phải nằm trong khoảng 1 đến 32.",
                        )
                current[key] = value
        _atomic_json_write(SETTINGS_FILE, current)
        _apply_runtime_settings(current)
        return {"ok": True, "settings": {key: current.get(key) for key in sorted(SETTINGS_KEYS)}}

    @app.get("/api/logs", dependencies=protected)
    def logs(limit: int = Query(default=250, ge=20, le=2000)) -> dict:
        try:
            with LOG_FILE.open("r", encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()[-limit:]
            modified_at = datetime.fromtimestamp(LOG_FILE.stat().st_mtime).isoformat(
                timespec="seconds"
            )
        except OSError:
            lines = []
            modified_at = ""
        return {
            "lines": [line.rstrip("\r\n") for line in lines],
            "updated_at": modified_at,
        }

    return app


def _bound_socket(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
