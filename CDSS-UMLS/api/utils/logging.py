"""Logging configuration."""

import logging
import sys
from pathlib import Path

from api.config import settings

# Create logs directory
Path("logs").mkdir(exist_ok=True)


def setup_logging():
    """Configure application logging."""
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # File handler
    file_handler = logging.FileHandler(settings.LOG_FILE)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(log_format, date_format))

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return root_logger
