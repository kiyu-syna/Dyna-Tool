import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.models.schemas import PLANS
from app.services import admin_service
from app.utils.admin_auth import (
    is_admin_logged_in,
    login_admin,
    logout_admin,
    require_admin_redirect,
    verify_admin_credentials,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_admin_logged_in(request):
        return RedirectResponse(url="/admin/", status_code=303)
    return templates.TemplateResponse(request, "admin/login.html", {"error": None})


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if verify_admin_credentials(username, password):
        login_admin(request)
        return RedirectResponse(url="/admin/", status_code=303)

    return templates.TemplateResponse(
        request,
        "admin/login.html",
        {"error": "Sai tên đăng nhập hoặc mật khẩu"},
        status_code=401,
    )


@router.get("/logout")
async def logout(request: Request):
    logout_admin(request)
    return RedirectResponse(url="/admin/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    tab: str = "overview",
    order_status: Optional[str] = None,
    msg: Optional[str] = None,
):
    redirect = require_admin_redirect(request)
    if redirect:
        return redirect

    stats = await admin_service.get_dashboard_stats()
    subscriptions = await admin_service.list_subscriptions()
    orders = await admin_service.list_orders(status=order_status or None)
    webhooks = await admin_service.list_webhook_logs()
    users = await admin_service.list_users()

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "tab": tab,
            "stats": stats,
            "subscriptions": subscriptions,
            "orders": orders,
            "webhooks": webhooks,
            "users": users,
            "plans": PLANS,
            "order_status": order_status or "",
            "msg": msg,
        },
    )


@router.post("/subscriptions/extend")
async def extend_subscription(
    request: Request,
    username: str = Form(...),
    days: int = Form(...),
    plan_name: str = Form(""),
):
    redirect = require_admin_redirect(request)
    if redirect:
        return redirect

    try:
        await admin_service.extend_subscription(
            username.strip().lower(),
            days,
            plan_name.strip() or None,
        )
        return RedirectResponse(
            url="/admin/?tab=subscriptions&msg=extended",
            status_code=303,
        )
    except ValueError as e:
        return RedirectResponse(
            url=f"/admin/?tab=subscriptions&msg=error:{e}",
            status_code=303,
        )


@router.post("/subscriptions/revoke")
async def revoke_subscription(request: Request, username: str = Form(...)):
    redirect = require_admin_redirect(request)
    if redirect:
        return redirect

    await admin_service.revoke_subscription(username.strip().lower())
    return RedirectResponse(url="/admin/?tab=subscriptions&msg=revoked", status_code=303)


@router.post("/users/delete")
async def delete_user(request: Request, username: str = Form(...)):
    redirect = require_admin_redirect(request)
    if redirect:
        return redirect

    await admin_service.delete_user(username)
    return RedirectResponse(url="/admin/?tab=users&msg=user_deleted", status_code=303)


@router.post("/webhooks/delete")
async def delete_webhook(request: Request, reference_code: str = Form(...)):
    redirect = require_admin_redirect(request)
    if redirect:
        return redirect

    await admin_service.delete_webhook_log(reference_code.strip())
    return RedirectResponse(url="/admin/?tab=webhooks&msg=deleted", status_code=303)
