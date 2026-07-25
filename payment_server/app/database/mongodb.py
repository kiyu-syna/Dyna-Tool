from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING
from app.config import get_settings
import logging

logger = logging.getLogger(__name__)
settings = get_settings()

client: AsyncIOMotorClient = None


async def _migrate_license_to_username(db):
    """Chuyển subscription từ machine_id sang username (một lần)."""
    async for sub in db.subscriptions.find({"username": {"$exists": False}}):
        username = sub.get("username")
        if username:
            continue
        mid = sub.get("machine_id")
        if not mid:
            continue
        user = await db.users.find_one({"machine_id": mid})
        if not user:
            user = await db.users.find_one({"username": mid.lower()}) if isinstance(mid, str) else None
        if user:
            await db.subscriptions.update_one(
                {"_id": sub["_id"]},
                {"$set": {"username": user["username"]}, "$unset": {"machine_id": ""}},
            )
            logger.info(f"[MIGRATE] subscription → user={user['username']}")

    async for order in db.orders.find({"username": {"$exists": False}, "machine_id": {"$exists": True}}):
        user = await db.users.find_one({"machine_id": order.get("machine_id")})
        if user:
            await db.orders.update_one(
                {"_id": order["_id"]},
                {"$set": {"username": user["username"]}},
            )


async def connect_db():
    global client
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    # Tạo indexes
    await db.orders.create_index([("order_id", ASCENDING)], unique=True)
    await db.orders.create_index([("username", ASCENDING)])
    await db.orders.create_index([("created_at", DESCENDING)])
    await db.orders.create_index([("status", ASCENDING)])

    await _migrate_license_to_username(db)

    for idx_name in ("machine_id_1", "username_1"):
        try:
            await db.subscriptions.drop_index(idx_name)
        except Exception:
            pass

    await db.subscriptions.create_index([("username", ASCENDING)], unique=True)
    await db.subscriptions.create_index([("expires_at", ASCENDING)])

    await db.webhook_logs.create_index([("reference_code", ASCENDING)], unique=True)

    await db.users.create_index([("username", ASCENDING)], unique=True)
    await db.users.create_index([("phone", ASCENDING)], unique=True)
    await db.users.create_index([("session_token", ASCENDING)])
    await db.telegram_links.create_index([("username", ASCENDING)], unique=True)
    await db.telegram_links.create_index([("chat_id", ASCENDING)], unique=True)
    await db.telegram_link_codes.create_index([("code", ASCENDING)], unique=True)
    await db.telegram_link_codes.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
    await db.telegram_caption_requests.create_index([("request_id", ASCENDING)], unique=True)
    await db.telegram_caption_requests.create_index([("username", ASCENDING), ("updated_at", DESCENDING)])
    await db.telegram_caption_requests.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
    await db.telegram_remote_actions.create_index([("request_id", ASCENDING)], unique=True)
    await db.telegram_remote_actions.create_index([("username", ASCENDING), ("status", ASCENDING), ("confirmed_at", ASCENDING)])
    await db.telegram_remote_actions.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
    logger.info("✅ MongoDB connected & indexes created")
    return db


async def disconnect_db():
    global client
    if client:
        client.close()
        logger.info("MongoDB disconnected")


def get_db():
    return client[settings.DB_NAME]
