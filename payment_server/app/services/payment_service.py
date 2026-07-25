import random
import re
import string
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from pymongo import ReturnDocument
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.database.mongodb import get_db
from app.models.schemas import PLANS, CreateOrderResponse
from app.config import get_settings
from app.utils.datetime_utils import ensure_utc
from urllib.parse import quote

logger = logging.getLogger(__name__)
settings = get_settings()

ORDER_ID_RE = re.compile(r"DT(\d{6})", re.IGNORECASE)


def _extract_order_id(payload: dict) -> Optional[str]:
    """Tìm mã đơn DT###### trong content/code/description (ngân hàng format khác nhau)."""
    parts = [
        payload.get("content") or "",
        payload.get("code") or "",
        payload.get("description") or "",
    ]
    for text in parts:
        m = ORDER_ID_RE.search(str(text).upper())
        if m:
            return f"DT{m.group(1)}"
    return None


def _generate_order_id() -> str:
    """Tạo order_id dạng DT483921"""
    digits = "".join(random.choices(string.digits, k=6))
    return f"DT{digits}"


def _build_vietqr_url(amount: int, content: str) -> str:
    """Tạo URL VietQR động cho MB Bank"""
    encoded_content = quote(content)
    encoded_name    = quote(settings.BANK_ACCOUNT_NAME)
    return (
        f"https://img.vietqr.io/image/"
        f"{settings.BANK_ID}-{settings.BANK_ACCOUNT_NO}-compact2.png"
        f"?amount={amount}&addInfo={encoded_content}&accountName={encoded_name}"
    )


async def _resolve_order_username(order: dict, *, session=None) -> Optional[str]:
    """Lấy username từ đơn (hỗ trợ đơn cũ chỉ có machine_id)."""
    if order.get("username"):
        return order["username"]
    db = get_db()
    mid = order.get("machine_id")
    if mid:
        user = await db.users.find_one({"machine_id": mid}, session=session)
        if user:
            return user["username"]
    return None


async def create_order(username: str, days: int) -> CreateOrderResponse:
    db = get_db()
    username = username.strip().lower()

    if days not in PLANS:
        raise ValueError(f"Gói {days} ngày không hợp lệ")
    plan = PLANS[days]

    # Generate unique order_id
    for _ in range(10):
        order_id = _generate_order_id()
        if not await db.orders.find_one({"order_id": order_id}):
            break
    else:
        raise RuntimeError("Không thể tạo order_id duy nhất")

    now        = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.ORDER_EXPIRY_MINUTES)

    doc = {
        "order_id":       order_id,
        "username":       username,
        "days":           days,
        "plan_name":      plan["name"],
        "amount":         plan["price"],
        "status":         "pending",
        "created_at":     now,
        "expires_at":     expires_at,
        "paid_at":        None,
        "reference_code": None,
    }
    await db.orders.insert_one(doc)
    logger.info(f"[ORDER] Created {order_id} | {plan['name']} | {plan['price']:,}đ | user={username}")

    qr_url = _build_vietqr_url(plan["price"], order_id)

    return CreateOrderResponse(
        order_id         = order_id,
        username         = username,
        days             = days,
        plan_name        = plan["name"],
        amount           = plan["price"],
        transfer_content = order_id,
        qr_url           = qr_url,
        bank_id          = settings.BANK_ID,
        account_no       = settings.BANK_ACCOUNT_NO,
        account_name     = settings.BANK_ACCOUNT_NAME,
        expires_at_order = expires_at,
        status           = "pending",
    )


async def get_payment_status(order_id: str) -> Optional[dict]:
    db  = get_db()
    doc = await db.orders.find_one({"order_id": order_id})
    if not doc:
        return None

    sub_expires = None
    if doc["status"] == "paid":
        uname = await _resolve_order_username(doc)
        if uname:
            sub = await db.subscriptions.find_one({"username": uname})
            if sub:
                sub_expires = sub.get("expires_at")

    return {
        "order_id":                  doc["order_id"],
        "status":                    doc["status"],
        "amount":                    doc["amount"],
        "paid_at":                   doc.get("paid_at"),
        "plan_name":                 doc["plan_name"],
        "subscription_expires_at":   sub_expires,
    }


async def _apply_webhook_payment(
    db, payload: dict, reference_code: str, order_id: str, now: datetime, session
) -> dict:
    """Validate and apply a payment inside an active MongoDB transaction."""
    log_result = await db.webhook_logs.update_one(
        {"reference_code": reference_code},
        {"$setOnInsert": {
            "reference_code": reference_code,
            "payload": payload,
            "received_at": now,
        }},
        upsert=True,
        session=session,
    )
    is_retry = log_result.upserted_id is None

    order = await db.orders.find_one({"order_id": order_id}, session=session)
    if not order:
        logger.warning("[WEBHOOK] Order %s does not exist", order_id)
        return {"success": False, "reason": "order_not_found"}

    if order.get("status") == "paid":
        logger.warning("[WEBHOOK] Order %s is already paid", order_id)
        return {
            "success": False,
            "reason": "duplicate" if is_retry else "already_paid",
        }

    if order.get("status") != "pending":
        logger.warning("[WEBHOOK] Order %s is not pending", order_id)
        return {"success": False, "reason": "order_not_pending"}

    if ensure_utc(order["expires_at"]) < now:
        await db.orders.update_one(
            {"order_id": order_id, "status": "pending"},
            {"$set": {"status": "expired"}},
            session=session,
        )
        logger.warning("[WEBHOOK] Order %s has expired", order_id)
        return {"success": False, "reason": "order_expired"}

    transfer_amount = int(payload.get("transferAmount", 0))
    if transfer_amount < order["amount"]:
        logger.warning(
            "[WEBHOOK] Amount mismatch: received %s < required %s",
            transfer_amount,
            order["amount"],
        )
        return {"success": False, "reason": "amount_mismatch"}

    username = await _resolve_order_username(order, session=session)
    if not username:
        logger.warning("[WEBHOOK] Order %s has no username", order_id)
        return {"success": False, "reason": "no_username"}

    days = order["days"]
    claimed_order = await db.orders.find_one_and_update(
        {"order_id": order_id, "status": "pending"},
        {"$set": {
            "status": "paid",
            "paid_at": now,
            "reference_code": reference_code,
            "username": username,
        }},
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    if claimed_order is None:
        logger.warning("[WEBHOOK] Order %s was claimed concurrently", order_id)
        return {"success": False, "reason": "already_paid"}

    existing_sub = await db.subscriptions.find_one(
        {"username": username}, session=session
    )
    previous_expiry = (
        ensure_utc(existing_sub.get("expires_at")) if existing_sub else None
    )
    base = previous_expiry if previous_expiry and previous_expiry > now else now
    sub_expires = base + timedelta(days=days)

    await db.subscriptions.update_one(
        {"username": username},
        {"$set": {
            "username": username,
            "plan_name": order["plan_name"],
            "days": days,
            "activated_at": now,
            "expires_at": sub_expires,
            "last_order_id": order_id,
        }},
        upsert=True,
        session=session,
    )

    logger.info(
        "[WEBHOOK] Order %s PAID | user=%s | license until %s",
        order_id,
        username,
        sub_expires.strftime("%d/%m/%Y"),
    )
    return {
        "success": True,
        "order_id": order_id,
        "username": username,
        "expires_at": sub_expires.isoformat(),
    }


async def process_webhook(payload: dict, reference_code: str) -> dict:
    """Process a SePay webhook as one atomic payment operation."""
    transfer_type = (payload.get("transferType") or "in").lower()
    if transfer_type == "out":
        logger.info("[WEBHOOK] Ignoring outgoing transfer")
        return {"success": True, "reason": "ignored_outgoing"}

    order_id = _extract_order_id(payload)
    if not order_id:
        logger.warning("[WEBHOOK] No order id found in payload")
        return {"success": False, "reason": "no_order_id"}

    db = get_db()
    now = datetime.now(timezone.utc)

    async def apply_payment(session):
        return await _apply_webhook_payment(
            db, payload, reference_code, order_id, now, session
        )

    # Fail closed when transactions are unavailable. SePay can retry a 500;
    # silently falling back to separate writes could charge a customer twice.
    async with await db.client.start_session() as session:
        return await session.with_transaction(
            apply_payment,
            read_concern=ReadConcern("snapshot"),
            write_concern=WriteConcern("majority"),
        )
