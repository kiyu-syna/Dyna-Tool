import logging
import logging.config
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.database.mongodb import connect_db, disconnect_db
from app.routers.payment import router
from app.routers.admin import router as admin_router
from app.routers.auth import router as auth_router
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
    logger.info("🚀 Dyna Tool Payment Server đang khởi động...")
    await connect_db()
    yield
    logger.info("🛑 Đang tắt server...")
    await disconnect_db()


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title       = "Dyna Tool Payment API",
    description = "Hệ thống thanh toán tự động cho Dyna Tool — tích hợp SePay + MongoDB",
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

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.ADMIN_SECRET_KEY,
    max_age=60 * 60 * 24 * 7,
)

_static_dir = __import__("pathlib").Path(__file__).resolve().parent / "static"
app.mount("/admin/static", StaticFiles(directory=str(_static_dir)), name="admin-static")

app.include_router(router)
app.include_router(auth_router)
app.include_router(admin_router)


@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "service": "Dyna Tool Payment API v1.0.0"}


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy"}
