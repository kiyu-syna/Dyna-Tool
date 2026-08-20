from datetime import datetime

from fastapi import HTTPException

from desktop_backend.schemas import (
    DouyinSelectionCompletePayload,
    DouyinSelectionCreatePayload,
    ExtensionJobPayload,
)
from application.publishing.extension_upload_service import ExtensionUploadRequest


def register_routes(app, context, extension_protected, *, APP_VERSION: str) -> None:
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

    @app.get(
        "/api/extension/selections/active",
        dependencies=extension_protected,
    )
    def extension_active_selection() -> dict:
        return {"session": context.douyin_selections.active(extension=True)}

    @app.post(
        "/api/extension/selections",
        dependencies=extension_protected,
        status_code=201,
    )
    def extension_create_selection(
        payload: DouyinSelectionCreatePayload,
    ) -> dict:
        try:
            session = context.douyin_selections.create(payload.source_url)
            return {"ok": True, "session": session}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post(
        "/api/extension/selections/{session_id}/complete",
        dependencies=extension_protected,
        status_code=202,
    )
    def extension_complete_selection(
        session_id: str,
        payload: DouyinSelectionCompletePayload,
    ) -> dict:
        try:
            session = context.douyin_selections.complete(
                session_id,
                [item.model_dump() for item in payload.items],
            )
            return {"ok": True, "session": session}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

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
