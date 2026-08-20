from fastapi import HTTPException

from desktop_backend.schemas import CreateProfilePayload, ProfilePayload, SeenStatePayload, SourceTestPayload
from application.tracking.sources import get_tracking_sources


def register_routes(app, context, protected) -> None:
    @app.get("/api/profiles/{profile_id}", dependencies=protected)
    def profile_detail(profile_id: str) -> dict:
        try:
            profile = context.profiles.load(profile_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"profile": context.profiles.redact_secrets(profile)}

    @app.post("/api/profiles", dependencies=protected)
    def create_profile(payload: CreateProfilePayload) -> dict:
        try:
            profile = context.profiles.create(payload.id, payload.name)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "profile": context.profiles.redact_secrets(profile)}

    @app.put("/api/profiles/{profile_id}", dependencies=protected)
    def save_profile(profile_id: str, payload: ProfilePayload) -> dict:
        try:
            profile = context.profiles.save(profile_id, payload.profile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "profile": context.profiles.redact_secrets(profile)}

    @app.post("/api/profiles/{profile_id}/sources/test", dependencies=protected)
    def test_profile_source(profile_id: str, payload: SourceTestPayload) -> dict:
        try:
            profile = context.profiles.load(profile_id)
            candidate_profile = {**profile, "tracking_sources": [payload.source]}
            sources = get_tracking_sources(candidate_profile)
            if not sources:
                raise ValueError("Nguồn theo dõi chưa có định danh hợp lệ.")

            from application.workflows.profile_worker import ProfileWorker

            worker = ProfileWorker(candidate_profile)
            source = sources[0]
            monitor = worker._create_monitor(source)
            videos = monitor.fetch_latest_videos(pages_to_fetch=1)
            return {
                "ok": bool(videos),
                "source": source,
                "message": (
                    f"Đã tìm thấy {len(videos)} video hợp lệ."
                    if videos
                    else "Không tìm thấy video thường hợp lệ; photo và video ghim đã bỏ qua."
                ),
                "videos": [
                    {
                        "video_id": str(video.aweme_id),
                        "share_url": str(video.share_url),
                        "desc": str(video.desc or ""),
                        "create_time": int(video.create_time or 0),
                        "like_count": int(video.like_count or 0),
                        "duration_seconds": float(video.duration_seconds),
                    }
                    for video in videos[:4]
                ],
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

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
