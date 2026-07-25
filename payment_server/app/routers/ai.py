"""Authenticated API for the Dyna desktop AI assistant."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.ai_gateway_service import (
    AiGatewayBadRequestError,
    AiGatewayConfigurationError,
    AiGatewayRateLimitError,
    AiGatewayUnavailableError,
    get_ai_gateway,
)
from app.services.license_service import get_license_status
from app.utils.user_deps import require_user

router = APIRouter(prefix="/api/ai", tags=["AI Assistant"])


class AiMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class AiChatRequest(BaseModel):
    messages: list[AiMessage] = Field(min_length=1, max_length=20)
    context: dict[str, Any] = Field(default_factory=dict)


@router.post("/chat")
async def chat(req: AiChatRequest, user: dict = Depends(require_user)):
    settings = get_settings()
    username = str(user.get("username") or "").strip().lower()
    if settings.AI_REQUIRE_ACTIVE_LICENSE:
        license_status = await get_license_status(username)
        if not license_status.get("is_active"):
            raise HTTPException(status_code=403, detail="Trợ lý AI yêu cầu gói Dyna Premium còn hiệu lực")
    try:
        return await get_ai_gateway().chat(
            username=username,
            messages=[message.model_dump() for message in req.messages],
            context=req.context,
        )
    except AiGatewayRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except AiGatewayBadRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AiGatewayConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AiGatewayUnavailableError as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(status_code=503, detail=str(exc), headers=headers) from exc
