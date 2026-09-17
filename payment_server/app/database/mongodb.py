from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING
from app.config import get_settings
import logging

logger = logging.getLogger(__name__)
settings = get_settings()

client: AsyncIOMotorClient = None


async def connect_db():
    global client
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    # Chỉ tạo các index phục vụ Telegram. Các collection thương mại cũ không
    # còn được đọc, ghi hoặc di chuyển bởi ứng dụng.
    await db.telegram_links.create_index([("username", ASCENDING)], unique=True)
    await db.telegram_links.create_index([("chat_id", ASCENDING)], unique=True)
    await db.telegram_link_codes.create_index([("code", ASCENDING)], unique=True)
    await db.telegram_link_codes.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
    await db.telegram_caption_requests.create_index([("request_id", ASCENDING)], unique=True)
    await db.telegram_caption_requests.create_index([("username", ASCENDING), ("updated_at", DESCENDING)])
    caption_indexes = await db.telegram_caption_requests.index_information()
    for index_name, index_config in caption_indexes.items():
        if index_config.get("expireAfterSeconds") is not None:
            await db.telegram_caption_requests.drop_index(index_name)
    await db.telegram_remote_actions.create_index([("request_id", ASCENDING)], unique=True)
    await db.telegram_remote_actions.create_index([("username", ASCENDING), ("status", ASCENDING), ("confirmed_at", ASCENDING)])
    await db.telegram_remote_actions.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
    logger.info("✅ MongoDB connected; Telegram indexes created")
    return db


async def disconnect_db():
    global client
    if client:
        client.close()
        logger.info("MongoDB disconnected")


def get_db():
    return client[settings.DB_NAME]
