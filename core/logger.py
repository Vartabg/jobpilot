"""
Logger — structured logging for JobPilot.

Provides a configured logger with:
  - Rich console handler for pretty terminal output
  - Rotating file handler to data/jobpilot.log (max 5MB × 3 backups)
  - Module-scoped getLogger() for use across the codebase
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

DATA_DIR = Path(__file__).parent.parent / "data"
LOG_FILE = DATA_DIR / "jobpilot.log"

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root JobPilot logger. Safe to call multiple times."""
    global _configured
    if _configured:
        return
    _configured = True

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("jobpilot")
    root.setLevel(level)

    # Rich console handler — short format for interactive use
    console_handler = RichHandler(
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console_handler)

    # Rotating file handler — detailed format for debugging
    file_handler = RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the 'jobpilot' namespace.

    Usage::

        from jobpilot.core.logger import get_logger
        log = get_logger(__name__)
        log.info("Connected to Chrome")
    """
    setup_logging()
    return logging.getLogger(f"jobpilot.{name}")
