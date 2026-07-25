from fastapi import HTTPException, Query

from desktop_backend.schemas import JobActionPayload, ManualPublishPayload, PublisherReadyCheckPayload
from services.publishing.manual_publish_service import ManualPublishItem, ManualPublishRequest, ManualPublishTarget


def register_routes(app, context, protected) -> None:
    @app.get("/api/publisher/profiles", dependencies=protected)
    def publisher_profiles() -> dict:
        return {"profiles": context.manual_publish.list_profiles()}

    @app.get("/api/publisher/jobs", dependencies=protected)
    def publisher_jobs(limit: int = Query(default=100, ge=1, le=500)) -> dict:
        return context.manual_publish.list_jobs(limit=limit)

    @app.get("/api/publisher/readiness", dependencies=protected)
    def publisher_readiness() -> dict:
        return context.manual_publish.readiness_snapshot()

    @app.post("/api/publisher/readiness/refresh", dependencies=protected, status_code=202)
    def publisher_refresh_readiness(payload: PublisherReadyCheckPayload) -> dict:
        try:
            return context.manual_publish.refresh_readiness(
                tuple(
                    ManualPublishTarget(
                        profile_id=target.profile_id,
                        platforms=tuple(target.platforms),
                    )
                    for target in payload.targets
                ),
                force=payload.force,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/publisher/jobs", dependencies=protected, status_code=202)
    def publisher_submit(payload: ManualPublishPayload) -> dict:
        try:
            return context.manual_publish.submit(
                ManualPublishRequest(
                    file_paths=tuple(payload.file_paths),
                    caption=payload.caption,
                    items=tuple(
                        ManualPublishItem(
                            file_path=item.file_path,
                            caption=item.caption,
                            scheduled_at=item.scheduled_at,
                        )
                        for item in payload.items
                    ),
                    targets=tuple(
                        ManualPublishTarget(
                            profile_id=target.profile_id,
                            platforms=tuple(target.platforms),
                        )
                        for target in payload.targets
                    ),
                )
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/api/publisher/jobs/retry", dependencies=protected)
    def publisher_retry(payload: JobActionPayload) -> dict:
        try:
            job = context.manual_publish.retry(payload.profile_id, payload.video_id)
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if job is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy video Publish Center.")
        return {"ok": True, "job": job}

    @app.post("/api/publisher/jobs/cancel", dependencies=protected)
    def publisher_cancel(payload: JobActionPayload) -> dict:
        job = context.manual_publish.cancel(payload.profile_id, payload.video_id)
        if job is None:
            raise HTTPException(status_code=409, detail="Video đang được xử lý hoặc không thể hủy.")
        return {"ok": True, "job": job}

    @app.post("/api/publisher/jobs/delete", dependencies=protected)
    def publisher_delete(payload: JobActionPayload) -> dict:
        try:
            job = context.manual_publish.delete(payload.profile_id, payload.video_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if job is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy video Publish Center.")
        return {"ok": True, "job": job}

    @app.post("/api/publisher/jobs/reset", dependencies=protected)
    def publisher_reset_jobs() -> dict:
        try:
            result = context.manual_publish.reset_jobs()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"ok": True, **result}
