import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.database.mongodb import get_db
from app.models.schemas import PLANS
from app.utils.datetime_utils import ensure_utc
from app.utils.mongo_utils import serialize_doc, serialize_docs

logger = logging.getLogger(__name__)


async def get_dashboard_stats() -> dict[str, Any]:
    db = get_db()
    now = datetime.now(timezone.utc)

    total_subs = await db.subscriptions.count_documents({})
    active_subs = await db.subscriptions.count_documents({"expires_at": {"$gt": now}})
    total_orders = await db.orders.count_documents({})
    pending_orders = await db.orders.count_documents({"status": "pending"})
    paid_orders = await db.orders.count_documents({"status": "paid"})
    webhook_logs = await db.webhook_logs.count_documents({})
    total_users = await db.users.count_documents({})

    revenue_cursor = db.orders.aggregate([
        {"$match": {"status": "paid"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ])
    revenue_rows = await revenue_cursor.to_list(1)
    total_revenue = revenue_rows[0]["total"] if revenue_rows else 0

    return {
        "total_subscriptions": total_subs,
        "active_subscriptions": active_subs,
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "paid_orders": paid_orders,
        "webhook_logs": webhook_logs,
        "total_users": total_users,
        "total_revenue": total_revenue,
    }


async def list_subscriptions(limit: int = 100) -> list[dict]:
    db = get_db()
    now = datetime.now(timezone.utc)
    cursor = db.subscriptions.find().sort("expires_at", -1).limit(limit)
    rows = []
    async for doc in cursor:
        item = serialize_doc(doc)
        if not item.get("username") and item.get("machine_id"):
            item["username"] = item["machine_id"]
        exp = ensure_utc(doc.get("expires_at"))
        item["is_active"] = exp is not None and exp > now
        if exp:
            item["days_remaining"] = max(0, (exp - now).days)
        else:
            item["days_remaining"] = 0
        rows.append(item)
    return rows


async def list_orders(limit: int = 100, status: Optional[str] = None) -> list[dict]:
    db = get_db()
    query: dict[str, Any] = {}
    if status:
        query["status"] = status
    cursor = db.orders.find(query).sort("created_at", -1).limit(limit)
    return serialize_docs([doc async for doc in cursor])


async def list_webhook_logs(limit: int = 100) -> list[dict]:
    db = get_db()
    cursor = db.webhook_logs.find().sort("received_at", -1).limit(limit)
    return serialize_docs([doc async for doc in cursor])


async def extend_subscription(username: str, days: int, plan_name: Optional[str] = None) -> dict:
    if days not in PLANS and days <= 0:
        raise ValueError("Số ngày không hợp lệ")

    username = username.strip().lower()
    db = get_db()
    now = datetime.now(timezone.utc)
    plan_label = plan_name or PLANS.get(days, {}).get("name", f"Gói {days} ngày")

    existing = await db.subscriptions.find_one({"username": username})
    if existing and ensure_utc(existing.get("expires_at")) and ensure_utc(existing["expires_at"]) > now:
        base = ensure_utc(existing["expires_at"])
    else:
        base = now

    new_expires = base + timedelta(days=days)

    await db.subscriptions.update_one(
        {"username": username},
        {"$set": {
            "username": username,
            "plan_name": plan_label,
            "days": days,
            "activated_at": now,
            "expires_at": new_expires,
            "last_order_id": existing.get("last_order_id") if existing else "ADMIN_GRANT",
        }},
        upsert=True,
    )
    logger.info(f"[ADMIN] Gia hạn {username} +{days} ngày → {new_expires}")
    return serialize_doc(await db.subscriptions.find_one({"username": username}))


async def revoke_subscription(username: str) -> bool:
    db = get_db()
    username = username.strip().lower()
    result = await db.subscriptions.delete_one({"username": username})
    logger.info(f"[ADMIN] Thu hồi license {username} (deleted={result.deleted_count})")
    return result.deleted_count > 0


async def delete_webhook_log(reference_code: str) -> bool:
    db = get_db()
    result = await db.webhook_logs.delete_one({"reference_code": reference_code})
    return result.deleted_count > 0


async def list_users(limit: int = 100) -> list[dict]:
    db = get_db()
    cursor = db.users.find(
        {},
        {"password_hash": 0, "session_token": 0},
    ).sort("created_at", -1).limit(limit)
    return serialize_docs([doc async for doc in cursor])


async def delete_user(username: str) -> bool:
    db = get_db()
    result = await db.users.delete_one({"username": username.strip().lower()})
    return result.deleted_count > 0
