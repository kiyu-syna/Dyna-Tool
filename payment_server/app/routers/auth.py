import logging
from fastapi import APIRouter, Header, HTTPException

from app.models.schemas import (
    AuthLoginRequest,
    AuthRegisterRequest,
    AuthResponse,
    AuthVerifyResponse,
)
from app.services import auth_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["Auth"])


@router.post("/register", response_model=AuthResponse)
async def register(req: AuthRegisterRequest):
    try:
        return await auth_service.register_user(req.phone, req.username, req.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login", response_model=AuthResponse)
async def login(req: AuthLoginRequest):
    try:
        return await auth_service.login_user(req.username, req.password)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.get("/verify", response_model=AuthVerifyResponse)
async def verify(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "").strip()
    user = await auth_service.verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Token không hợp lệ hoặc đã hết hạn")
    return {"valid": True, **user}
