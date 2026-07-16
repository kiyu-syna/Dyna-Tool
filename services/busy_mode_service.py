from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

import core.config as config


BUSY_MODE_FILE = Path(config.BASE_DIR) / "state" / "busy_mode.json"
_BUSY_MODE_LOCK = threading.RLock()


def _default_state() -> dict:
    return {"busy": False, "source": "default", "updated_at": ""}


def get_busy_mode_state() -> dict:
    with _BUSY_MODE_LOCK:
        if not BUSY_MODE_FILE.is_file():
            return _default_state()
        try:
            payload = json.loads(BUSY_MODE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return _default_state()
    if not isinstance(payload, dict):
        return _default_state()
    return {
        "busy": bool(payload.get("busy", False)),
        "source": str(payload.get("source") or "unknown"),
        "updated_at": str(payload.get("updated_at") or ""),
    }


def is_busy_mode_enabled() -> bool:
    return bool(get_busy_mode_state()["busy"])


def set_busy_mode(enabled: bool, *, source: str = "unknown") -> dict:
    state = {
        "busy": bool(enabled),
        "source": str(source or "unknown"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _BUSY_MODE_LOCK:
        BUSY_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = BUSY_MODE_FILE.with_name(
            f"{BUSY_MODE_FILE.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, BUSY_MODE_FILE)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return dict(state)
