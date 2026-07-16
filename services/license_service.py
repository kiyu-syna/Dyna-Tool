"""
license_manager.py
──────────────────
Quản lý license theo tài khoản đăng nhập (username).
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import requests
import core.config as config

logger = logging.getLogger(__name__)

BASE_DIR = config.BASE_DIR
LICENSE_FILE = os.path.join(BASE_DIR, "license.json")


def _get_api_base_url() -> str:
    url = os.environ.get("DYNATOOL_API_URL", "").strip()
    if url:
        return url.rstrip("/")

    settings_path = config.SETTINGS_FILE
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            url = (data.get("PAYMENT_API_URL") or data.get("DYNATOOL_API_URL") or "").strip()
            if url:
                return url.rstrip("/")
        except Exception:
            pass

    return "http://localhost:8000"


API_BASE_URL = _get_api_base_url()


def get_api_base_url() -> str:
    """Return the current configured API URL instead of the import-time value."""
    return _get_api_base_url()


def _current_username() -> Optional[str]:
    import services.auth_service as auth_service
    user = auth_service.get_current_user()
    return user.get("username") if user else None


def _auth_headers() -> dict:
    import services.auth_service as auth_service
    user = auth_service.get_current_user()
    if not user or not user.get("token"):
        raise ValueError("Cần đăng nhập để sử dụng tính năng Premium")
    return {"Authorization": f"Bearer {user['token']}"}


def _license_file_path() -> str:
    uname = _current_username()
    if uname:
        return os.path.join(BASE_DIR, f"license_{uname}.json")
    return LICENSE_FILE


def _load_local() -> Optional[dict]:
    try:
        path = _license_file_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _save_local(data: dict):
    try:
        with open(_license_file_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[license] Cannot save license file: {e}")


def is_licensed_local() -> bool:
    """Kiểm tra license local của tài khoản đang đăng nhập."""
    uname = _current_username()
    if not uname:
        return False

    data = _load_local()
    if not data or data.get("username") != uname:
        return False

    try:
        exp = data["expires_at"]
        if isinstance(exp, str):
            exp = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < exp
    except Exception:
        return False


def get_license_info() -> Optional[dict]:
    return _load_local()


def verify_with_server() -> dict:
    """Xác thực license của tài khoản đang đăng nhập."""
    uname = _current_username()
    if not uname:
        return {"username": "", "is_active": False}

    try:
        resp = requests.post(
            f"{get_api_base_url()}/api/license/check",
            headers=_auth_headers(),
            json={},
            timeout=8,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("is_active"):
                exp = data.get("expires_at")
                exp_str = exp if isinstance(exp, str) else (
                    exp.isoformat() if exp else ""
                )
                _save_local({
                    "username": data.get("username", uname),
                    "plan_name": data.get("plan_name"),
                    "expires_at": exp_str,
                    "days_remaining": data.get("days_remaining"),
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                })
            return data
        if resp.status_code == 401:
            return {"username": uname, "is_active": False, "error": "session_expired"}
    except requests.exceptions.ConnectionError:
        # Mất kết nối máy chủ license là trạng thái tạm thời; tiếp tục dùng cache cục bộ.
        pass
    except ValueError as e:
        return {"username": uname, "is_active": False, "error": str(e)}
    except Exception as e:
        logger.error(f"[license] Error verifying license: {e}")

    local = _load_local()
    if local and local.get("username") == uname:
        return {**local, "is_active": is_licensed_local(), "source": "local_cache"}
    return {"username": uname, "is_active": False}


def create_order(days: int) -> dict:
    """Tạo đơn hàng cho tài khoản đang đăng nhập."""
    resp = requests.post(
        f"{get_api_base_url()}/api/create-order",
        headers=_auth_headers(),
        json={"days": days},
        timeout=10,
    )
    if resp.status_code == 401:
        raise ValueError("Phiên đăng nhập hết hạn. Vui lòng đăng nhập lại.")
    resp.raise_for_status()
    return resp.json()


def poll_payment_status(order_id: str) -> dict:
    resp = requests.get(
        f"{get_api_base_url()}/api/payment-status/{order_id}",
        timeout=8,
    )
    resp.raise_for_status()
    return resp.json()


def save_license_from_server() -> dict:
    data = verify_with_server()
    if data.get("is_active"):
        _save_local(data)
    return data

