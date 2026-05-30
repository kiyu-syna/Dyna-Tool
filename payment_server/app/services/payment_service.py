import random
import re
import string
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

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


async def _resolve_order_username(order: dict) -> Optional[str]:
    """Lấy username từ đơn (hỗ trợ đơn cũ chỉ có machine_id)."""
    if order.get("username"):
        return order["username"]
    db = get_db()
    mid = order.get("machine_id")
    if mid:
        user = await db.users.find_one({"machine_id": mid})
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


async def process_webhook(payload: dict, reference_code: str) -> dict:
    """
    Xử lý SePay webhook:
    1. Chống duplicate bằng referenceCode
    2. Parse content → order_id (DT######)
    3. Kiểm tra amount khớp
    4. Kích hoạt license
    """
    db = get_db()

    transfer_type = (payload.get("transferType") or "in").lower()
    if transfer_type == "out":
        logger.info("[WEBHOOK] Bỏ qua giao dịch tiền ra (transferType=out)")
        return {"success": True, "reason": "ignored_outgoing"}

    # ── Parse order_id từ content / code / description ────────────────────────
    content = payload.get("content") or ""
    order_id = _extract_order_id(payload)

    # ── Chống duplicate webhook (cho phép retry nếu đơn vẫn pending) ─────────
    existing = await db.webhook_logs.find_one({"reference_code": reference_code})
    if existing:
        order = await db.orders.find_one({"order_id": order_id}) if order_id else None
        if not (order and order.get("status") == "pending"):
            logger.warning(f"[WEBHOOK] Duplicate referenceCode={reference_code}, bỏ qua")
            return {"success": False, "reason": "duplicate"}
        logger.info(f"[WEBHOOK] Retry referenceCode={reference_code} cho order {order_id}")
    else:
        await db.webhook_logs.insert_one({
            "reference_code": reference_code,
            "payload":        payload,
            "received_at":    datetime.now(timezone.utc),
        })

    if not order_id:
        logger.warning(
            f"[WEBHOOK] Không tìm thấy mã đơn DT###### | content='{content}' | "
            f"code='{payload.get('code')}' | description='{payload.get('description')}'"
        )
        return {"success": False, "reason": "no_order_id"}

    # ── Tìm order ─────────────────────────────────────────────────────────────
    order = await db.orders.find_one({"order_id": order_id})
    if not order:
        logger.warning(f"[WEBHOOK] Order {order_id} không tồn tại")
        return {"success": False, "reason": "order_not_found"}

    if order["status"] == "paid":
        logger.warning(f"[WEBHOOK] Order {order_id} đã thanh toán rồi")
        return {"success": False, "reason": "already_paid"}

    now = datetime.now(timezone.utc)
    if ensure_utc(order["expires_at"]) < now:
        await db.orders.update_one({"order_id": order_id}, {"$set": {"status": "expired"}})
        logger.warning(f"[WEBHOOK] Order {order_id} đã hết hạn")
        return {"success": False, "reason": "order_expired"}

    # ── Kiểm tra số tiền ──────────────────────────────────────────────────────
    transfer_amount = int(payload.get("transferAmount", 0))
    if transfer_amount < order["amount"]:
        logger.warning(
            f"[WEBHOOK] Amount mismatch: nhận {transfer_amount:,} < yêu cầu {order['amount']:,}"
        )
        return {"success": False, "reason": "amount_mismatch"}

    # ── Kích hoạt license theo tài khoản ──────────────────────────────────────
    username = await _resolve_order_username(order)
    if not username:
        logger.warning(f"[WEBHOOK] Order {order_id} không có username (đơn cũ?)")
        return {"success": False, "reason": "no_username"}

    days = order["days"]
    sub_expires = now + timedelta(days=days)

    await db.orders.update_one(
        {"order_id": order_id},
        {"$set": {
            "status": "paid",
            "paid_at": now,
            "reference_code": reference_code,
            "username": username,
        }},
    )

    existing_sub = await db.subscriptions.find_one({"username": username})
    sub_expires_prev = ensure_utc(existing_sub.get("expires_at")) if existing_sub else None
    if sub_expires_prev and sub_expires_prev > now:
        sub_expires = sub_expires_prev + timedelta(days=days)

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
    )

    logger.info(
        f"[WEBHOOK] ✅ Order {order_id} PAID | user={username} | "
        f"license đến {sub_expires.strftime('%d/%m/%Y')}"
    )
    return {
        "success": True,
        "order_id": order_id,
        "username": username,
        "expires_at": sub_expires.isoformat(),
    }
