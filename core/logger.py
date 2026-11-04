"""
Logger — structured logging for JobPilot.

Provides a configured logger with:
  - Rich console handler for pretty terminal output
  - Rotating file handler to data/jobpilot.log (max 5MB × 3 backups)
  - Module-scoped getLogger() for use across the codebase
"""

import logging
import re
from logging.handlers import RotatingFileHandler

from rich.logging import RichHandler

from jobpilot.core.config import DATA_DIR

LOG_FILE = DATA_DIR / "jobpilot.log"

_configured = False

# Defense-in-depth for the DEBUG file log: JobPilot handles resumes, contact
# details, and API keys, so a stray log line must never persist a credential
# or an email address. Patterns classify by credential shape/assignment, not
# by one product's key name, so renamed keys are still caught.
_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[redacted-email]"),
    (re.compile(r"AIza[0-9A-Za-z_-]{20,}"), "[redacted-key]"),
    (re.compile(r"(?i)\bBearer\s+\S+"), "Bearer [redacted]"),
    (
        re.compile(
            r"(?i)([\w-]*(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret))(\s*[:=]\s*)\S+"
        ),
        r"\1\2[redacted]",
    ),
    (re.compile(r"(?i)([?&]key=)[^&\s]+"), r"\1[redacted]"),
]


def redact_text(text: str) -> str:
    """Mask emails and credential-shaped values in a log message."""
    out = text
    for pattern, replacement in _REDACTIONS:
        out = pattern.sub(replacement, out)
    return out


class RedactingFilter(logging.Filter):
    """Logging filter that redacts credentials/PII before any handler writes."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_text(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


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
    console_handler.addFilter(RedactingFilter())
    root.addHandler(console_handler)

    # Rotating file handler — detailed format for debugging
    file_handler = RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(RedactingFilter())
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
