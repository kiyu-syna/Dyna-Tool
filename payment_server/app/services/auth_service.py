import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.database.mongodb import get_db
from app.utils.password_utils import hash_password, verify_password

logger = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")
PHONE_RE = re.compile(r"^0?\d{9,10}$")
TOKEN_TTL_DAYS = 30


def _normalize_phone(phone: str) -> str:
    p = re.sub(r"\D", "", phone.strip())
    if p.startswith("84") and len(p) >= 11:
        p = "0" + p[2:]
    if len(p) == 9:
        p = "0" + p
    return p


def _validate_credentials(username: str, password: str, phone: Optional[str] = None):
    if not USERNAME_RE.match(username):
        raise ValueError("Tên đăng nhập: 3–32 ký tự, chỉ chữ, số và _")
    if len(password) < 6:
        raise ValueError("Mật khẩu tối thiểu 6 ký tự")
    if phone is not None:
        if not PHONE_RE.match(_normalize_phone(phone)):
            raise ValueError("Số điện thoại không hợp lệ")


async def register_user(phone: str, username: str, password: str) -> dict:
    username = username.strip().lower()
    phone = _normalize_phone(phone)
    _validate_credentials(username, password, phone)

    db = get_db()
    if await db.users.find_one({"username": username}):
        raise ValueError("Tên đăng nhập đã tồn tại")
    if await db.users.find_one({"phone": phone}):
        raise ValueError("Số điện thoại đã được đăng ký")

    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(48)
    doc = {
        "username": username,
        "phone": phone,
        "password_hash": hash_password(password),
        "session_token": token,
        "token_expires_at": now + timedelta(days=TOKEN_TTL_DAYS),
        "created_at": now,
        "last_login_at": now,
    }
    await db.users.insert_one(doc)
    logger.info(f"[AUTH] Đăng ký user={username}")
    return _public_user(doc, token)


async def login_user(username: str, password: str) -> dict:
    username = username.strip().lower()
    _validate_credentials(username, password)

    db = get_db()
    user = await db.users.find_one({"username": username})
    if not user or not verify_password(password, user.get("password_hash", "")):
        raise ValueError("Sai tên đăng nhập hoặc mật khẩu")

    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(48)
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "session_token": token,
            "token_expires_at": now + timedelta(days=TOKEN_TTL_DAYS),
            "last_login_at": now,
        }},
    )
    user["session_token"] = token
    user["token_expires_at"] = now + timedelta(days=TOKEN_TTL_DAYS)
    logger.info(f"[AUTH] Đăng nhập user={username}")
    return _public_user(user, token)


async def verify_token(token: str) -> Optional[dict]:
    if not token:
        return None
    db = get_db()
    user = await db.users.find_one({"session_token": token})
    if not user:
        return None
    exp = user.get("token_expires_at")
    if exp and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp and exp < datetime.now(timezone.utc):
        return None
    return _public_user(user, token)


def _public_user(user: dict, token: str) -> dict:
    return {
        "token": token,
        "username": user["username"],
        "phone": user.get("phone", ""),
    }
