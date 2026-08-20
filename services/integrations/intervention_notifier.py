from __future__ import annotations

import os
from datetime import datetime

from core.runtime_paths import logs_dir
from core.utils import logger
from services.integrations.telegram_service import send_screenshot_notification


def take_screenshot(page=None, filename: str = "error_screenshot.png") -> str | None:
    save_path = logs_dir() / filename
    try:
        if page is not None:
            try:
                page.screenshot(path=str(save_path))
                return str(save_path)
            except Exception as exc:
                logger.debug("Không thể chụp trang bằng Playwright: %s", exc)

        import pyautogui

        screenshot = pyautogui.screenshot()
        screenshot.save(str(save_path))
        return str(save_path)
    except Exception as exc:
        logger.warning("Không thể chụp ảnh màn hình: %s", exc)
        return None


def notify_intervention(reason: str, page=None) -> bool:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_path = take_screenshot(page, f"intervention_{timestamp}.png")
    if not image_path:
        return False
    try:
        send_screenshot_notification(image_path, reason)
        return True
    finally:
        try:
            os.remove(image_path)
        except OSError:
            pass
