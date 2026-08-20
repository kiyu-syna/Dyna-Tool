from collections import Counter
from typing import Any

from fastapi import HTTPException

import core.config as config
from desktop_backend.schemas import (
    AssistantCaptionPayload,
    AssistantChatPayload,
    AssistantConfirmPayload,
)
from services.assistant.ai_assistant_service import AiAssistantError
from application.publishing.manual_publish_service import ManualPublishTarget


def register_routes(app, context, protected, *, _profile_summary) -> None:
    def assistant_context() -> dict[str, Any]:
        jobs = context.jobs.list_jobs()
        active_profile_ids = {
            str(job.get("profile_id")) for job in jobs if job.get("active")
        }
        active_profile_ids.update(context.runtime.active_profile_ids())
        profile_configs = config.load_profile_configs()
        profiles = [
            _profile_summary(str(profile_id), profile, jobs, active_profile_ids)
            for profile_id, profile in sorted(
                profile_configs.items(), key=lambda item: str(item[0]).zfill(8)
            )
        ]
        readiness = context.manual_publish.readiness_snapshot()
        checks = [
            {
                key: row.get(key)
                for key in ("profile_id", "platform", "status", "ready", "message", "checked_at")
            }
            for row in (readiness.get("checks") or [])
            if isinstance(row, dict)
        ]
        publish_rows = context.manual_publish.list_jobs(limit=500).get("jobs") or []
        publish_statuses = Counter(str(row.get("status") or "unknown") for row in publish_rows)
        return {
            "profiles": profiles,
            "readiness": {
                "checking": bool(readiness.get("checking")),
                "checking_profiles": readiness.get("checking_profiles") or [],
                "checks": checks,
            },
            "publish_center": {
                "total_jobs": len(publish_rows),
                "status_counts": dict(publish_statuses),
            },
        }

    @app.post("/api/assistant/chat", dependencies=protected)
    def assistant_chat(payload: AssistantChatPayload) -> dict:
        try:
            return context.assistant.chat(
                [message.model_dump() for message in payload.messages],
                assistant_context(),
            )
        except AiAssistantError as exc:
            headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
            raise HTTPException(
                status_code=exc.status_code,
                detail=str(exc),
                headers=headers,
            ) from exc

    @app.post("/api/assistant/captions/generate", dependencies=protected)
    def assistant_generate_caption(payload: AssistantCaptionPayload) -> dict:
        try:
            return context.assistant.generate_caption(
                original_description=payload.original_description,
                instruction=payload.instruction,
                video_label=payload.video_label,
            )
        except AiAssistantError as exc:
            headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
            raise HTTPException(
                status_code=exc.status_code,
                detail=str(exc),
                headers=headers,
            ) from exc

    @app.post("/api/assistant/actions/confirm", dependencies=protected)
    def assistant_confirm(payload: AssistantConfirmPayload) -> dict:
        try:
            actions = context.assistant.consume_proposal(payload.proposal_token)
        except AiAssistantError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        results: list[dict[str, Any]] = []
        for action in actions:
            action_type = action["type"]
            args = action["args"]
            try:
                if action_type == "start_profile":
                    result = context.runtime.start_profile(args["profile_id"])
                elif action_type == "stop_profile":
                    result = context.runtime.stop_profile(args["profile_id"])
                elif action_type == "refresh_readiness":
                    result = context.manual_publish.refresh_readiness(
                        tuple(
                            ManualPublishTarget(
                                profile_id=target["profile_id"],
                                platforms=tuple(target["platforms"]),
                            )
                            for target in args["targets"]
                        ),
                        force=True,
                    )
                else:
                    raise ValueError("Hành động AI không được hỗ trợ")
                results.append({"type": action_type, "ok": True, "result": result})
            except Exception as exc:
                results.append({"type": action_type, "ok": False, "error": str(exc)})
        return {
            "ok": bool(results) and all(item["ok"] for item in results),
            "results": results,
        }
