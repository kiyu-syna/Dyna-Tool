from fastapi import HTTPException

from desktop_backend.schemas import BrowserRuntimeInstallPayload, LocalProfileCheckPayload, LocalProfileSetupPayload
from services.browser.browser_runtime_service import BrowserRuntimeError
from services.browser.local_profile_setup_service import LocalProfileSetupError


def register_routes(app, context, protected, *, hooks) -> None:
    @app.get("/api/browser-runtimes", dependencies=protected)
    def browser_runtimes() -> dict:
        return {"runtimes": list_browser_runtimes()}

    @app.post("/api/browser-runtimes/install", dependencies=protected)
    def install_managed_browser_runtime(payload: BrowserRuntimeInstallPayload) -> dict:
        try:
            runtime = hooks.install_browser_runtime(
                payload.source_executable,
                runtime_id=payload.runtime_id,
            )
        except (BrowserRuntimeError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "runtime": runtime.to_dict()}

    @app.post("/api/browser-runtimes/install-playwright", dependencies=protected)
    def install_default_chromium_runtime() -> dict:
        try:
            runtime = hooks.install_playwright_chromium_runtime()
        except (BrowserRuntimeError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "runtime": runtime.to_dict()}

    @app.get("/api/browser-profiles/sessions", dependencies=protected)
    def local_profile_setup_sessions() -> dict:
        return {"sessions": context.local_profiles.snapshot()}

    @app.post("/api/browser-profiles/{profile_id}/check", dependencies=protected)
    def check_local_profile(profile_id: str, payload: LocalProfileCheckPayload) -> dict:
        try:
            state = context.local_profiles.check(
                profile_id,
                repair_stale_locks=payload.repair_stale_locks,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (ValueError, LocalProfileSetupError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"ok": True, "state": state}

    @app.post("/api/browser-profiles/{profile_id}/initialize", dependencies=protected)
    def initialize_local_profile(profile_id: str, payload: LocalProfileSetupPayload) -> dict:
        if str(profile_id) in context.runtime.active_profile_ids():
            raise HTTPException(status_code=409, detail="Hãy dừng Profile trước khi tạo Local Chromium.")
        try:
            state = context.local_profiles.start(
                profile_id,
                executable_path=payload.executable_path,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (ValueError, LocalProfileSetupError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"ok": True, "state": state}

    @app.post("/api/browser-profiles/{profile_id}/finish", dependencies=protected)
    def finish_local_profile(profile_id: str) -> dict:
        try:
            state = context.local_profiles.finish(profile_id)
        except (ValueError, LocalProfileSetupError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"ok": True, "state": state}

    @app.post("/api/browser-profiles/{profile_id}/open", dependencies=protected)
    def open_existing_local_profile(profile_id: str) -> dict:
        if str(profile_id) in context.runtime.active_profile_ids():
            raise HTTPException(status_code=409, detail="Hãy dừng Profile trước khi mở đăng nhập.")
        try:
            state = context.local_profiles.open_existing(profile_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (ValueError, LocalProfileSetupError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"ok": True, "state": state}

