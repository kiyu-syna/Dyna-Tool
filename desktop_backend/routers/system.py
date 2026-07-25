from datetime import datetime
import os


def register_routes(app, protected, *, APP_VERSION: str) -> None:
    @app.get("/api/health", dependencies=protected)
    def health() -> dict:
        return {
            "ok": True,
            "version": APP_VERSION,
            "pid": os.getpid(),
            "time": datetime.now().isoformat(timespec="seconds"),
        }
