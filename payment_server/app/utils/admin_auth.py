import hmac
from fastapi import Request
from fastapi.responses import RedirectResponse

from app.config import get_settings

settings = get_settings()


def verify_admin_credentials(username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(username.strip(), settings.ADMIN_USERNAME)
    pass_ok = hmac.compare_digest(password, settings.ADMIN_PASSWORD)
    return user_ok and pass_ok


def is_admin_logged_in(request: Request) -> bool:
    return request.session.get("admin") is True


def login_admin(request: Request) -> None:
    request.session["admin"] = True


def logout_admin(request: Request) -> None:
    request.session.clear()


def require_admin_redirect(request: Request) -> RedirectResponse | None:
    if not is_admin_logged_in(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    return None
