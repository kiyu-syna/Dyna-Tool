"""Local desktop API for the shared Telegram bot."""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.services.telegram_bot_service import get_telegram_bot_service
from app.services.telegram_remote_action_service import get_telegram_remote_action_service
router = APIRouter(prefix="/api/telegram", tags=["Telegram"])
LOCAL_OWNER = "local"


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


@router.get("/status")
async def status():
    return await get_telegram_bot_service().connection_status(LOCAL_OWNER)


@router.post("/link-code")
async def link_code():
    try:
        return await get_telegram_bot_service().create_link_code(LOCAL_OWNER)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/link")
async def unlink():
    await get_telegram_bot_service().unlink(LOCAL_OWNER)
    return {"ok": True}


@router.post("/notifications")
async def notification(payload: NotificationRequest):
    try:
        delivered = await get_telegram_bot_service().send_notification(
            LOCAL_OWNER, payload.text, cancel_job=payload.cancel_job,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"delivered": delivered}


@router.post("/diagnostics")
async def diagnostic_image(
    text: str = Form(min_length=1, max_length=1024),
    image: UploadFile = File(...),
):
    image_bytes = await image.read(10 * 1024 * 1024 + 1)
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Ảnh chẩn đoán vượt quá 10 MB")
    try:
        delivered = await get_telegram_bot_service().send_diagnostic_image(
            LOCAL_OWNER,
            text,
            image_bytes,
            image.filename or "diagnostic.png",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"delivered": delivered}


@router.post("/remote-actions/claim")
async def claim_remote_action():
    action = await get_telegram_remote_action_service().claim_next(LOCAL_OWNER)
    if not action:
        return {"action": None}
    return {
        "action": {
            "request_id": action["request_id"],
            "actions": action.get("actions") or [],
        }
    }


@router.post("/remote-actions/complete")
async def complete_remote_action(payload: RemoteActionCompletion):
    action = await get_telegram_remote_action_service().complete(
        LOCAL_OWNER,
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
        await get_telegram_bot_service().send_notification(LOCAL_OWNER, text)
    except RuntimeError:
        # Desktop execution was successful even if Telegram is temporarily down.
        pass
    return {"accepted": True}


@router.post("/captions")
async def create_caption(payload: CaptionRequest):
    try:
        return await get_telegram_bot_service().create_caption_request(LOCAL_OWNER, payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/captions/{request_id}")
async def caption_status(request_id: str):
    return await get_telegram_bot_service().caption_status(LOCAL_OWNER, request_id)
