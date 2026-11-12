"""Structured, rotating file + console logging."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_CONFIGURED = False
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    """Configure root logging once (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(_LOG_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "camerasmart.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)

    # Tame noisy third-party loggers. ``aiosqlite`` logs *every* statement at
    # DEBUG (a firehose next to what the app itself logs); SQL debugging is done
    # properly with ``CAMERASMART_SQL_ECHO=true``.
    for name in ("uvicorn.access", "multipart", "watchfiles", "aiosqlite"):
        logging.getLogger(name).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
