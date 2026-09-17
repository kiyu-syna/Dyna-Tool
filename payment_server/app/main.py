import logging
import logging.config
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database.mongodb import connect_db, disconnect_db
from app.routers.telegram import router as telegram_router
from app.services.telegram_bot_service import get_telegram_bot_service
from app.config import get_settings

settings = get_settings()

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level   = logging.DEBUG if settings.DEBUG else logging.INFO,
    format  = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Dyna Telegram Server đang khởi động...")
    await connect_db()
    await get_telegram_bot_service().start()
    yield
    await get_telegram_bot_service().stop()
    logger.info("🛑 Đang tắt server...")
    await disconnect_db()


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title       = "Dyna Telegram API",
    description = "Dịch vụ Telegram cục bộ cho Dyna Tool",
    version     = "1.0.0",
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

_cors_origins = [
    o.strip()
    for o in settings.CORS_ORIGINS.split(",")
    if o.strip()
]
if not _cors_origins:
    _cors_origins = [
        settings.SERVER_URL.rstrip("/"),
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins     = _cors_origins,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

app.include_router(telegram_router)


@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "service": "Dyna Telegram API v1.0.0"}


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy"}
