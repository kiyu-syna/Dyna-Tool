from fastapi import Query

from services.runtime.log_view_service import build_log_snapshot, reset_log_files


def register_routes(app, context, protected, *, hooks) -> None:
    @app.get("/api/logs", dependencies=protected)
    def logs(
        limit: int = Query(default=2000, ge=20, le=5000),
        cursor: str = Query(default="", max_length=160),
    ) -> dict:
        return build_log_snapshot(
            hooks.LOG_FILE,
            limit=limit,
            profiles=context.profiles,
            cursor=cursor,
        )

    @app.delete("/api/logs", dependencies=protected)
    def reset_logs() -> dict:
        return {"ok": True, **reset_log_files(hooks.LOG_FILE)}
