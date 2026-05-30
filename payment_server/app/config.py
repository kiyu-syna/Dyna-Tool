from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # MongoDB
    MONGODB_URL: str = "mongodb://localhost:27017"
    DB_NAME: str = "dynatool"

    # SePay webhook security
    SEPAY_WEBHOOK_SECRET: str = "your_sepay_webhook_secret"

    # Bank info (MB Bank)
    BANK_ID: str = "MB"
    BANK_ACCOUNT_NO: str = "your_account_number"
    BANK_ACCOUNT_NAME: str = "NGUYEN VAN A"

    # Server
    SERVER_URL: str = "http://localhost:8000"
    # Comma-separated origins for CORS (e.g. https://app.example.com,http://localhost:3000)
    CORS_ORIGINS: str = ""
    DEBUG: bool = False

    # Order expiry (minutes)
    ORDER_EXPIRY_MINUTES: int = 30

    # Admin panel
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "change_me_admin_2026"
    ADMIN_SECRET_KEY: str = "change_me_session_secret_key_32chars"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
