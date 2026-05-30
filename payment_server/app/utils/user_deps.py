from fastapi import Header, HTTPException

from app.services import auth_service


async def require_user(authorization: str = Header(default="")) -> dict:
    token = authorization.replace("Bearer ", "").strip()
    user = await auth_service.verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Cần đăng nhập để sử dụng tính năng này")
    return user
