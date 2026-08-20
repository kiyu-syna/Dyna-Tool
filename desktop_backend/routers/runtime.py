from fastapi import HTTPException

import core.config as config
from desktop_backend.schemas import DiagnosticPayload, TestUploadPayload
from application.tracking.profile_diagnostics_service import run_profile_diagnostics
from services.runtime.overview_service import build_overview_snapshot


def register_routes(app, context, protected, *, _profile_summary) -> None:
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
        context.manual_publish.stop_scheduler()
        context.local_profiles.shutdown()
        return {"ok": True, **context.runtime.shutdown()}

