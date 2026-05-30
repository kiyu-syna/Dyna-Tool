import hmac
import logging
from fastapi import Request, HTTPException, status
from app.config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()


async def verify_sepay_webhook(request: Request) -> str:
    """
    Xác thực SePay webhook bằng API key.
    SePay gửi header: Authorization: Apikey <your_key>
    """
    auth = request.headers.get("Authorization", "")
    if not auth:
        logger.warning("[SECURITY] Webhook thiếu Authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    token = auth.replace("Apikey ", "", 1).replace("apikey ", "", 1).strip()
    if not hmac.compare_digest(token, settings.SEPAY_WEBHOOK_SECRET):
        logger.warning("[SECURITY] Sai API key SePay webhook")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return "ok"
