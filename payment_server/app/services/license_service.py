import logging
from datetime import datetime, timezone

from app.database.mongodb import get_db
from app.utils.datetime_utils import ensure_utc

logger = logging.getLogger(__name__)


async def get_license_status(username: str) -> dict:
    username = username.strip().lower()
    db = get_db()
    sub = await db.subscriptions.find_one({"username": username})
    if not sub:
        return {"username": username, "is_active": False}

    now = datetime.now(timezone.utc)
    expires_at = ensure_utc(sub.get("expires_at"))
    is_active = expires_at is not None and expires_at > now

    days_remaining = None
    if is_active and expires_at:
        days_remaining = max(0, (expires_at - now).days)

    return {
        "username": username,
        "is_active": is_active,
        "plan_name": sub.get("plan_name"),
        "expires_at": expires_at,
        "days_remaining": days_remaining,
    }
