from fastapi import HTTPException

import services.account.auth_service as auth_service
import services.account.license_service as license_service
from desktop_backend.schemas import CreateOrderPayload, LoginPayload, RegisterPayload


def register_routes(app, context, protected, *, LICENSE_PLANS, public_user) -> None:
    @app.get("/api/auth/status", dependencies=protected)
    def auth_status(verify: bool = True) -> dict:
        authenticated = auth_service.verify_session() if verify else auth_service.is_logged_in()
        user = auth_service.get_current_user() if authenticated else None
        return {"authenticated": authenticated, "user": public_user(user)}

    @app.post("/api/auth/login", dependencies=protected)
    def login(payload: LoginPayload) -> dict:
        try:
            user = auth_service.login(payload.username, payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Không kết nối được máy chủ: {exc}")
        return {"ok": True, "user": public_user(user)}

    @app.post("/api/auth/register", dependencies=protected)
    def register(payload: RegisterPayload) -> dict:
        try:
            user = auth_service.register(payload.phone, payload.username, payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Không kết nối được máy chủ: {exc}")
        return {"ok": True, "user": public_user(user)}

    @app.post("/api/auth/logout", dependencies=protected)
    def logout() -> dict:
        context.local_profiles.shutdown()
        context.runtime.shutdown()
        auth_service.logout()
        return {"ok": True}

    @app.get("/api/license", dependencies=protected)
    def license_status(refresh: bool = False) -> dict:
        info = license_service.verify_with_server() if refresh else license_service.get_license_info()
        return {
            "is_active": bool(license_service.is_licensed_local()),
            "info": info or {},
            "plans": LICENSE_PLANS,
        }

    @app.post("/api/license/verify", dependencies=protected)
    def verify_license() -> dict:
        info = license_service.save_license_from_server()
        return {"is_active": bool(info.get("is_active")), "info": info}

    @app.post("/api/license/orders", dependencies=protected)
    def create_license_order(payload: CreateOrderPayload) -> dict:
        allowed_days = {plan["days"] for plan in LICENSE_PLANS}
        if payload.days not in allowed_days:
            raise HTTPException(status_code=400, detail="Gói sử dụng không hợp lệ.")
        try:
            return license_service.create_order(payload.days)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Không thể tạo đơn hàng: {exc}")

    @app.get("/api/license/orders/{order_id}", dependencies=protected)
    def payment_status(order_id: str) -> dict:
        try:
            return license_service.poll_payment_status(order_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Không kiểm tra được thanh toán: {exc}")
