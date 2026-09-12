from __future__ import annotations

import logging
import os
import sys
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
log_handlers = [console_log_handler]
file_log_handler = None
if os.environ.get("DYNA_DISABLE_FILE_LOGGING", "").strip() != "1":
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
    log_handlers.append(file_log_handler)

logging.basicConfig(
    level="INFO",
    format="%(message)s",
    handlers=log_handlers,
)
logger = logging.getLogger("rich")
