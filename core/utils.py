from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

from rich.console import Console
from rich.logging import RichHandler

from core.runtime_paths import logs_dir


if getattr(sys, "frozen", False):
    cert_path = os.path.join(sys._MEIPASS, "certifi", "cacert.pem")
    if os.path.exists(cert_path):
        os.environ["SSL_CERT_FILE"] = cert_path
        os.environ["REQUESTS_CA_BUNDLE"] = cert_path

if os.name == "nt":
    for output_stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(output_stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass

console = Console()
log_directory = logs_dir()
log_directory.mkdir(parents=True, exist_ok=True)

console_log_handler = RichHandler(
    rich_tracebacks=True,
    markup=True,
    console=console,
)
file_log_handler = RotatingFileHandler(
    log_directory / "system.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
file_log_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)

logging.basicConfig(
    level="INFO",
    format="%(message)s",
    handlers=[console_log_handler, file_log_handler],
)
logger = logging.getLogger("rich")


def take_screenshot(page=None, filename: str = "error_screenshot.png") -> str | None:
    save_path = log_directory / filename
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
    from services.integrations.telegram_service import send_screenshot_notification

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
