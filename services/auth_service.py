"""
Auth service — Đăng nhập / đăng ký tài khoản DynaTool qua API server.
"""

import json
import os
import logging
from typing import Optional

import requests

import services.license_service as license_service
import core.config as config

logger = logging.getLogger(__name__)

BASE_DIR = config.BASE_DIR
AUTH_FILE = os.path.join(BASE_DIR, "auth.json")

_current_user: Optional[dict] = None


def _api_url(path: str) -> str:
    return f"{license_service.get_api_base_url()}{path}"


def _save_session(data: dict):
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _load_session() -> Optional[dict]:
    try:
        if os.path.exists(AUTH_FILE):
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def get_current_user() -> Optional[dict]:
    global _current_user
    if _current_user:
        return _current_user
    _current_user = _load_session()
    return _current_user


def is_logged_in() -> bool:
    return bool(get_current_user() and get_current_user().get("token"))


def logout():
    global _current_user
    _current_user = None
    try:
        if os.path.exists(AUTH_FILE):
            os.remove(AUTH_FILE)
    except Exception as e:
        logger.error(f"Không xóa được auth.json: {e}")


def verify_session() -> bool:
    global _current_user
    data = _load_session()
    if not data or not data.get("token"):
        return False
    try:
        resp = requests.get(
            _api_url("/api/auth/verify"),
            headers={"Authorization": f"Bearer {data['token']}"},
            timeout=8,
        )
        if resp.status_code == 200:
            body = resp.json()
            merged = {**data, **body}
            _save_session(merged)
            _current_user = merged
            return True
    except requests.exceptions.ConnectionError:
        _current_user = data
        return True
    except Exception as e:
        logger.warning(f"Verify session lỗi: {e}")
    return False


def try_auto_login() -> bool:
    if not _load_session():
        return False
    return verify_session()


def login(username: str, password: str) -> dict:
    global _current_user
    resp = requests.post(
        _api_url("/api/auth/login"),
        json={"username": username.strip(), "password": password},
        timeout=12,
    )
    if resp.status_code == 401:
        raise ValueError(resp.json().get("detail", "Sai tên đăng nhập hoặc mật khẩu"))
    resp.raise_for_status()
    data = resp.json()
    _save_session(data)
    _current_user = data
    return data


def register(phone: str, username: str, password: str) -> dict:
    global _current_user
    resp = requests.post(
        _api_url("/api/auth/register"),
        json={
            "phone": phone.strip(),
            "username": username.strip(),
            "password": password,
        },
        timeout=12,
    )
    if resp.status_code == 400:
        raise ValueError(resp.json().get("detail", "Đăng ký thất bại"))
    resp.raise_for_status()
    data = resp.json()
    _save_session(data)
    _current_user = data
    return data

