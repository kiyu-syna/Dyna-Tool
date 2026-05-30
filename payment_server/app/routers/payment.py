import logging
from fastapi import APIRouter, HTTPException, Request, Depends, status
from fastapi.responses import JSONResponse

from app.models.schemas import (
    CreateOrderRequest, CreateOrderResponse,
    PaymentStatusResponse, SepayWebhookPayload,
    LicenseStatusResponse,
)
from app.services import payment_service, license_service
from app.utils.security import verify_sepay_webhook
from app.utils.user_deps import require_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ── POST /api/create-order ────────────────────────────────────────────────────
@router.post("/api/create-order", response_model=CreateOrderResponse, tags=["Payment"])
async def create_order(req: CreateOrderRequest, user: dict = Depends(require_user)):
    """
    Tạo đơn hàng mới (yêu cầu đăng nhập). License gắn với tài khoản.

    Request body:
    ```json
    { "days": 30 }
    ```
    Header: `Authorization: Bearer <token>`
    """
    try:
        result = await payment_service.create_order(user["username"], req.days)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[CREATE_ORDER] Lỗi: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Lỗi server")


# ── GET /api/payment-status/{order_id} ───────────────────────────────────────
@router.get("/api/payment-status/{order_id}", response_model=PaymentStatusResponse, tags=["Payment"])
async def payment_status(order_id: str):
    """
    Kiểm tra trạng thái thanh toán của đơn hàng.

    Response khi chưa thanh toán:
    ```json
    { "order_id": "DT483921", "status": "pending", "amount": 249000, ... }
    ```
    Response khi đã thanh toán:
    ```json
    { "order_id": "DT483921", "status": "paid", "paid_at": "2025-01-01T...", ... }
    ```
    """
    result = await payment_service.get_payment_status(order_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy order {order_id}")
    return result


# ── SePay webhook (POST /payment/webhook hoặc /webhook) ───────────────────────
async def _handle_sepay_webhook(request: Request):
    """Nhận webhook từ SePay sau khi nhận tiền chuyển khoản."""
    await verify_sepay_webhook(request)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    payload = SepayWebhookPayload(**body)
    reference_code = payload.referenceCode
    if reference_code is None:
        if payload.id is not None:
            reference_code = f"sepay-{payload.id}"
        elif payload.code:
            reference_code = payload.code[:64]
        else:
            reference_code = (payload.content or "unknown")[:32]

    logger.info(
        f"[WEBHOOK] Nhận từ SePay | id={payload.id} | gateway={payload.gateway} | "
        f"type={payload.transferType} | amount={payload.transferAmount:,.0f} | "
        f"content='{payload.content}' | code='{payload.code}' | ref={reference_code}"
    )

    try:
        result = await payment_service.process_webhook(body, reference_code)
    except Exception as e:
        logger.error(f"[WEBHOOK] Lỗi xử lý: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Webhook processing failed")

    if result.get("success"):
        return JSONResponse(content={"success": True, "message": "Thanh toán thành công"})

    reason = result.get("reason", "unknown")
    logger.warning(f"[WEBHOOK] Không xử lý được: {reason}")

    if reason == "duplicate":
        return JSONResponse(content={"success": True, "message": "webhook_received"})
    if reason == "no_order_id" and payload.transferAmount == 0:
        return JSONResponse(content={"success": True, "message": "webhook_received"})

    return JSONResponse(content={"success": False, "reason": reason})


@router.get("/payment/webhook", tags=["Webhook"])
@router.get("/webhook", tags=["Webhook"])
async def sepay_webhook_ping():
    """Kiểm tra URL webhook còn sống (mở trên trình duyệt hoặc SePay ping)."""
    return {"success": True, "status": "webhook_ready"}


@router.post("/payment/webhook", tags=["Webhook"])
@router.post("/webhook", tags=["Webhook"])
async def sepay_webhook(request: Request):
    return await _handle_sepay_webhook(request)


# ── POST /api/license/check ───────────────────────────────────────────────────
@router.post("/api/license/check", response_model=LicenseStatusResponse, tags=["License"])
async def check_license(user: dict = Depends(require_user)):
    """
    Kiểm tra license của tài khoản đang đăng nhập.

    Header: `Authorization: Bearer <token>`
    """
    return await license_service.get_license_status(user["username"])
