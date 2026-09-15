"""Authenticated desktop API for the shared, server-owned Telegram bot."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.services.telegram_bot_service import get_telegram_bot_service
from app.services.telegram_remote_action_service import get_telegram_remote_action_service
from app.utils.user_deps import require_user

router = APIRouter(prefix="/api/telegram", tags=["Telegram"])


class NotificationRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    cancel_job: dict[str, str] | None = None


class CaptionRequest(BaseModel):
    profile_id: str = Field(default="", max_length=64)
    profile_name: str = Field(default="", max_length=256)
    source_label: str = Field(default="", max_length=512)
    video_id: str = Field(default="", max_length=256)
    description: str = Field(default="", max_length=1200)
    default_caption: str = Field(default="", max_length=10000)
    pin_message: bool = False


class RemoteActionResult(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    ok: bool
    error: str = Field(default="", max_length=800)


class RemoteActionCompletion(BaseModel):
    request_id: str = Field(min_length=12, max_length=128)
    results: list[RemoteActionResult] = Field(min_length=1, max_length=8)


def _username(user: dict) -> str:
    return str(user.get("username") or "").strip().lower()


@router.get("/status")
async def status(user: dict = Depends(require_user)):
    return await get_telegram_bot_service().connection_status(_username(user))


@router.post("/link-code")
async def link_code(user: dict = Depends(require_user)):
    try:
        return await get_telegram_bot_service().create_link_code(_username(user))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/link")
async def unlink(user: dict = Depends(require_user)):
    await get_telegram_bot_service().unlink(_username(user))
    return {"ok": True}


@router.post("/notifications")
async def notification(payload: NotificationRequest, user: dict = Depends(require_user)):
    try:
        delivered = await get_telegram_bot_service().send_notification(
            _username(user), payload.text, cancel_job=payload.cancel_job,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"delivered": delivered}


@router.post("/diagnostics")
async def diagnostic_image(
    text: str = Form(min_length=1, max_length=1024),
    image: UploadFile = File(...),
    user: dict = Depends(require_user),
):
    image_bytes = await image.read(10 * 1024 * 1024 + 1)
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Ảnh chẩn đoán vượt quá 10 MB")
    try:
        delivered = await get_telegram_bot_service().send_diagnostic_image(
            _username(user),
            text,
            image_bytes,
            image.filename or "diagnostic.png",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"delivered": delivered}


@router.post("/remote-actions/claim")
async def claim_remote_action(user: dict = Depends(require_user)):
    action = await get_telegram_remote_action_service().claim_next(_username(user))
    if not action:
        return {"action": None}
    return {
        "action": {
            "request_id": action["request_id"],
            "actions": action.get("actions") or [],
        }
    }


@router.post("/remote-actions/complete")
async def complete_remote_action(payload: RemoteActionCompletion, user: dict = Depends(require_user)):
    action = await get_telegram_remote_action_service().complete(
        _username(user),
        payload.request_id,
        [result.model_dump() for result in payload.results],
    )
    if not action:
        return {"accepted": False}

    succeeded = [result.type for result in payload.results if result.ok]
    failed = [result for result in payload.results if not result.ok]
    if failed:
        details = "\n".join(f"• {result.type}: {result.error or 'Không thực hiện được'}" for result in failed)
        text = "Dyna không thể hoàn tất một số thao tác:\n" + details
    else:
        text = "Dyna đã thực hiện xong thao tác đã xác nhận."
        if succeeded:
            text += "\n" + "\n".join(f"• {item}" for item in succeeded)
    try:
        await get_telegram_bot_service().send_notification(_username(user), text)
    except RuntimeError:
        # Desktop execution was successful even if Telegram is temporarily down.
        pass
    return {"accepted": True}


@router.post("/captions")
async def create_caption(payload: CaptionRequest, user: dict = Depends(require_user)):
    try:
        return await get_telegram_bot_service().create_caption_request(_username(user), payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/captions/{request_id}")
async def caption_status(request_id: str, user: dict = Depends(require_user)):
    return await get_telegram_bot_service().caption_status(_username(user), request_id)
