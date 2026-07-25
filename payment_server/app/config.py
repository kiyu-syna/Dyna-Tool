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

    # Dyna AI gateway. API keys stay on this server and are never sent to the app.
    AI_ENABLED: bool = True
    AI_REQUIRE_ACTIVE_LICENSE: bool = True
    AI_PROVIDER_ORDER: str = "gemini,groq,openrouter"
    # The plural fields are preferred and accept comma/semicolon/newline separated
    # keys.  The singular aliases make a one-key setup less surprising.
    AI_GEMINI_API_KEYS: str = ""
    AI_GROQ_API_KEYS: str = ""
    AI_OPENROUTER_API_KEYS: str = ""
    AI_GEMINI_API_KEY: str = ""
    AI_GROQ_API_KEY: str = ""
    AI_OPENROUTER_API_KEY: str = ""
    AI_GEMINI_MODEL: str = "gemini-3.5-flash"
    AI_GROQ_MODEL: str = "llama-3.3-70b-versatile"
    AI_OPENROUTER_MODEL: str = "openrouter/auto"
    AI_REQUEST_TIMEOUT_SECONDS: float = 45.0
    AI_MAX_OUTPUT_TOKENS: int = 1200
    AI_REQUESTS_PER_MINUTE: int = 12
    AI_OPENROUTER_SITE_URL: str = ""
    AI_OPENROUTER_APP_NAME: str = "Dyna AI"

    # Shared Telegram bot. This token is intentionally server-only: never
    # return it from an API and never put it in the desktop configuration.
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_BOT_USERNAME: str = ""
    TELEGRAM_POLLING_ENABLED: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
