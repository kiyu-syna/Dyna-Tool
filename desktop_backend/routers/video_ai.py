import secrets
from pathlib import Path

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse

from desktop_backend.schemas import (
    VideoAiDubbingPayload,
    VideoAiEditorSavePayload,
    VideoAiProjectCreatePayload,
    VideoAiRenderPayload,
    VideoAiSubtitleDetectionPayload,
    VideoAiSubtitlesSavePayload,
    VideoAiTtsPreparePayload,
    VideoAiTtsPreviewPayload,
    VideoAiTranscribePayload,
    VideoAiTranslatePayload,
)
from services.video_ai.video_ai_service import (
    VideoAiBusyError,
    VideoAiDependencyError,
    VideoAiError,
    VideoAiNotFoundError,
)


def _http_error(exc: VideoAiError) -> HTTPException:
    if isinstance(exc, VideoAiNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, VideoAiBusyError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, VideoAiDependencyError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def register_routes(app, context, protected) -> None:
    @app.get("/api/video-ai/projects/{project_id}/media")
    def video_ai_media(project_id: str, access_token: str = Query(default="")):
        if not context.token or not secrets.compare_digest(access_token, context.token):
            raise HTTPException(status_code=401, detail="Unauthorized")
        try:
            project = context.video_ai.get_project(project_id)
        except VideoAiError as exc:
            raise _http_error(exc) from exc
        source = Path(project["source_path"])
        if not source.is_file():
            raise HTTPException(status_code=404, detail="Không tìm thấy video nguồn.")
        media_type = {
            ".mp4": "video/mp4",
            ".m4v": "video/x-m4v",
            ".mov": "video/quicktime",
            ".webm": "video/webm",
        }.get(source.suffix.lower(), "application/octet-stream")
        return FileResponse(
            source,
            media_type=media_type,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    @app.get("/api/video-ai/projects/{project_id}/dubbing-audio")
    def video_ai_dubbing_audio(project_id: str, access_token: str = Query(default="")):
        if not context.token or not secrets.compare_digest(access_token, context.token):
            raise HTTPException(status_code=401, detail="Unauthorized")
        try:
            project = context.video_ai.get_project(project_id)
        except VideoAiError as exc:
            raise _http_error(exc) from exc
        audio_path = Path(
            ((project.get("dubbing_options") or {}).get("audio_path") or "")
        )
        if not audio_path.is_file():
            raise HTTPException(status_code=404, detail="Chưa có track lồng tiếng.")
        return FileResponse(
            audio_path,
            media_type="audio/wav",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    @app.get("/api/video-ai/tts/previews/{preview_id}")
    def video_ai_tts_preview_audio(
        preview_id: str,
        access_token: str = Query(default=""),
    ):
        if not context.token or not secrets.compare_digest(access_token, context.token):
            raise HTTPException(status_code=401, detail="Unauthorized")
        try:
            audio_path = context.video_ai.tts_preview_path(preview_id)
        except VideoAiError as exc:
            raise _http_error(exc) from exc
        return FileResponse(
            audio_path,
            media_type="audio/wav" if audio_path.suffix.lower() == ".wav" else "audio/mpeg",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    @app.get("/api/video-ai/capabilities", dependencies=protected)
    def video_ai_capabilities() -> dict:
        return context.video_ai.capabilities()

    @app.get("/api/video-ai/tts/status", dependencies=protected)
    def video_ai_tts_status(provider: str = Query(default="vieneu")) -> dict:
        return context.video_ai.tts_runtime_status(provider)

    @app.post("/api/video-ai/tts/prepare", dependencies=protected, status_code=202)
    def video_ai_tts_prepare(payload: VideoAiTtsPreparePayload) -> dict:
        try:
            return context.video_ai.start_tts_prepare(payload.provider)
        except VideoAiError as exc:
            raise _http_error(exc) from exc

    @app.post("/api/video-ai/tts/preview", dependencies=protected)
    def video_ai_tts_preview(payload: VideoAiTtsPreviewPayload) -> dict:
        try:
            return context.video_ai.create_tts_preview(
                provider=payload.provider,
                voice=payload.voice,
                style=payload.style,
                text=payload.text,
            )
        except VideoAiError as exc:
            raise _http_error(exc) from exc

    @app.get("/api/video-ai/projects", dependencies=protected)
    def video_ai_projects(limit: int = Query(default=20, ge=1, le=100)) -> dict:
        return {"projects": context.video_ai.list_projects(limit=limit)}

    @app.post("/api/video-ai/projects", dependencies=protected, status_code=201)
    def video_ai_create_project(payload: VideoAiProjectCreatePayload) -> dict:
        try:
            return {"project": context.video_ai.create_project(payload.source_path)}
        except VideoAiError as exc:
            raise _http_error(exc) from exc

    @app.get("/api/video-ai/projects/{project_id}", dependencies=protected)
    def video_ai_project(project_id: str) -> dict:
        try:
            return {"project": context.video_ai.get_project(project_id)}
        except VideoAiError as exc:
            raise _http_error(exc) from exc

    @app.put("/api/video-ai/projects/{project_id}/subtitles", dependencies=protected)
    def video_ai_save_subtitles(
        project_id: str,
        payload: VideoAiSubtitlesSavePayload,
    ) -> dict:
        try:
            return {
                "project": context.video_ai.save_subtitles(
                    project_id,
                    [item.model_dump() for item in payload.subtitles],
                )
            }
        except VideoAiError as exc:
            raise _http_error(exc) from exc

    @app.put("/api/video-ai/projects/{project_id}/editor", dependencies=protected)
    def video_ai_save_editor(
        project_id: str,
        payload: VideoAiEditorSavePayload,
    ) -> dict:
        try:
            return {
                "project": context.video_ai.save_editor_state(
                    project_id,
                    subtitles=[item.model_dump() for item in payload.subtitles],
                    blur=payload.blur.model_dump(),
                    style=payload.style.model_dump(),
                    dubbing=payload.dubbing.model_dump() if payload.dubbing else None,
                )
            }
        except VideoAiError as exc:
            raise _http_error(exc) from exc

    @app.post(
        "/api/video-ai/projects/{project_id}/detect-subtitle-region",
        dependencies=protected,
        status_code=202,
    )
    def video_ai_detect_subtitle_region(
        project_id: str,
        payload: VideoAiSubtitleDetectionPayload,
    ) -> dict:
        try:
            return {
                "project": context.video_ai.start_subtitle_detection(
                    project_id,
                    sample_count=payload.sample_count,
                )
            }
        except VideoAiError as exc:
            raise _http_error(exc) from exc

    @app.post(
        "/api/video-ai/projects/{project_id}/transcribe",
        dependencies=protected,
        status_code=202,
    )
    def video_ai_transcribe(project_id: str, payload: VideoAiTranscribePayload) -> dict:
        try:
            return {
                "project": context.video_ai.start_transcription(
                    project_id,
                    source_language=payload.source_language,
                    model_name=payload.model_name,
                )
            }
        except VideoAiError as exc:
            raise _http_error(exc) from exc

    @app.post(
        "/api/video-ai/projects/{project_id}/translate",
        dependencies=protected,
        status_code=202,
    )
    def video_ai_translate(project_id: str, payload: VideoAiTranslatePayload) -> dict:
        try:
            return {
                "project": context.video_ai.start_translation(
                    project_id,
                    target_language=payload.target_language,
                )
            }
        except VideoAiError as exc:
            raise _http_error(exc) from exc

    @app.post(
        "/api/video-ai/projects/{project_id}/dubbing",
        dependencies=protected,
        status_code=202,
    )
    def video_ai_dubbing(project_id: str, payload: VideoAiDubbingPayload) -> dict:
        try:
            return {
                "project": context.video_ai.start_dubbing(
                    project_id,
                    provider=payload.provider,
                    voice=payload.voice,
                    style=payload.style,
                    rate=payload.rate,
                    volume=payload.volume,
                    original_volume=18 if payload.original_volume is None else payload.original_volume,
                )
            }
        except VideoAiError as exc:
            raise _http_error(exc) from exc

    @app.post(
        "/api/video-ai/projects/{project_id}/render",
        dependencies=protected,
        status_code=202,
    )
    def video_ai_render(project_id: str, payload: VideoAiRenderPayload) -> dict:
        try:
            return {
                "project": context.video_ai.start_render(
                    project_id,
                    track=payload.track,
                    blur=payload.blur.model_dump(),
                    style=payload.style.model_dump(),
                    dubbing=payload.dubbing.model_dump() if payload.dubbing else None,
                    output_path=payload.output_path,
                )
            }
        except VideoAiError as exc:
            raise _http_error(exc) from exc

    @app.post("/api/video-ai/projects/{project_id}/cancel", dependencies=protected)
    def video_ai_cancel(project_id: str) -> dict:
        try:
            return {"project": context.video_ai.cancel(project_id)}
        except VideoAiError as exc:
            raise _http_error(exc) from exc
