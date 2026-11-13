from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config import settings


def configure_logging() -> None:
    settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.LOG_LEVEL)

    if not any(getattr(handler, "_cold_email_file", False) for handler in root_logger.handlers):
        file_handler = RotatingFileHandler(
            settings.LOGS_DIR / "app.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        file_handler._cold_email_file = True
        root_logger.addHandler(file_handler)

    if not any(getattr(handler, "_cold_email_console", False) for handler in root_logger.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        console_handler._cold_email_console = True
        root_logger.addHandler(console_handler)
