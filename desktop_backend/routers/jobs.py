from fastapi import HTTPException, Query

from desktop_backend.schemas import JobActionPayload


def register_routes(app, context, protected, *, _apply_job_source_display_names) -> None:
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
        _apply_job_source_display_names(rows)
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

    @app.post("/api/jobs/delete", dependencies=protected)
    def delete_job(payload: JobActionPayload) -> dict:
        try:
            job = context.jobs.dismiss_job(
                payload.profile_id,
                payload.video_id,
                reason="Người dùng xóa video khỏi hàng đợi desktop.",
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if job is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy video trong hàng đợi.")
        return {"ok": True, "job": job}

    @app.post("/api/jobs/reset", dependencies=protected)
    def reset_jobs() -> dict:
        try:
            result = context.jobs.dismiss_jobs(
                reason="Người dùng reset toàn bộ hàng đợi video từ giao diện desktop.",
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"ok": True, **result}
