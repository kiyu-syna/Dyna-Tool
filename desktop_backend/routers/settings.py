from fastapi import HTTPException

import core.config as config
from desktop_backend.schemas import SettingsPayload


def register_routes(
    app,
    protected,
    *,
    hooks,
    SETTINGS_KEYS,
    TELEGRAM_NOTIFICATION_TYPES,
    CONCURRENCY_SETTING_KEYS,
) -> None:
    @app.get("/api/settings", dependencies=protected)
    def settings() -> dict:
        current = config.load_settings()
        return {"settings": {key: current.get(key) for key in sorted(SETTINGS_KEYS)}}

    @app.get("/api/telegram/status", dependencies=protected)
    def telegram_status() -> dict:
        from services.integrations.telegram_service import telegram_connection_status

        return telegram_connection_status()

    @app.post("/api/telegram/link-code", dependencies=protected)
    def telegram_link_code() -> dict:
        from services.integrations.telegram_service import create_telegram_link_code

        try:
            return create_telegram_link_code()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.delete("/api/telegram/link", dependencies=protected)
    def telegram_unlink() -> dict:
        from services.integrations.telegram_service import unlink_telegram

        return {"ok": unlink_telegram()}

    @app.put("/api/settings", dependencies=protected)
    def save_settings(payload: SettingsPayload) -> dict:
        current = config.load_settings()
        for key, value in payload.settings.items():
            if key in SETTINGS_KEYS:
                if key == "UI_LANGUAGE" and value not in {"vi", "en", "zh", "zh-TW"}:
                    raise HTTPException(
                        status_code=400,
                        detail="UI_LANGUAGE phải là vi, en, zh hoặc zh-TW.",
                    )
                if key == "UI_THEME" and value not in {"system", "light", "dark"}:
                    raise HTTPException(
                        status_code=400,
                        detail="UI_THEME phải là system, light hoặc dark.",
                    )
                if key == "TELEGRAM_NOTIFICATION_TYPES":
                    if not isinstance(value, dict):
                        raise HTTPException(
                            status_code=400,
                            detail="TELEGRAM_NOTIFICATION_TYPES phải là một đối tượng.",
                        )
                    current_preferences = current.get("TELEGRAM_NOTIFICATION_TYPES") or {}
                    value = {
                        notification_type: bool(value.get(notification_type, current_preferences.get(notification_type, True)))
                        for notification_type in TELEGRAM_NOTIFICATION_TYPES
                    }
                if key in CONCURRENCY_SETTING_KEYS:
                    try:
                        value = int(value)
                    except (TypeError, ValueError) as exc:
                        raise HTTPException(
                            status_code=400,
                            detail=f"{key} phải là số nguyên.",
                        ) from exc
                    if not 1 <= value <= 32:
                        raise HTTPException(
                            status_code=400,
                            detail=f"{key} phải nằm trong khoảng 1 đến 32.",
                        )
                current[key] = value
        hooks._atomic_json_write(hooks.SETTINGS_FILE, current)
        hooks._apply_runtime_settings(current)
        return {"ok": True, "settings": {key: current.get(key) for key in sorted(SETTINGS_KEYS)}}
