"""
Configuration — single source of truth for all JobPilot constants.

Every tunable value lives here.  Modules import from this file
instead of defining their own magic numbers.
"""

import json
import os
import sys
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

# Load .env if python-dotenv is available (dev convenience).
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _editable_project_root() -> Path | None:
    """Return the source checkout recorded by an editable installation."""
    try:
        direct_url = distribution("jobpilot").read_text("direct_url.json")
        payload = json.loads(direct_url or "{}")
    except (PackageNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return None
    if not payload.get("dir_info", {}).get("editable"):
        return None
    try:
        parsed = urlsplit(str(payload.get("url", "")))
        if parsed.scheme != "file":
            return None
        root = Path(url2pathname(unquote(parsed.path)))
    except (TypeError, ValueError):
        return None
    return root if (root / "pyproject.toml").is_file() else None


def _data_dir_from_locations(
    *,
    package_root: Path,
    editable_root: Path | None,
    environ: Mapping[str, str],
    home: Path,
    platform_name: str,
) -> Path:
    """Choose a stable, writable state directory for the current runtime."""
    explicit = environ.get("JOBPILOT_DATA_DIR", "").strip()
    if explicit:
        configured = Path(explicit).expanduser()
        return configured if configured.is_absolute() else Path.cwd() / configured

    if (package_root / "pyproject.toml").is_file():
        return package_root / "data"
    if editable_root is not None:
        return editable_root / "data"

    if platform_name == "darwin":
        return home / "Library" / "Application Support" / "JobPilot"
    if os.name == "nt" and environ.get("LOCALAPPDATA"):
        return Path(environ["LOCALAPPDATA"]) / "JobPilot"
    xdg_data_home = environ.get("XDG_DATA_HOME", "").strip()
    return (Path(xdg_data_home).expanduser() if xdg_data_home else home / ".local" / "share") / "jobpilot"


DATA_DIR = _data_dir_from_locations(
    package_root=Path(__file__).resolve().parent.parent,
    editable_root=_editable_project_root(),
    environ=os.environ,
    home=Path.home(),
    platform_name=sys.platform,
)
SESSION_FILE = DATA_DIR / "session.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

# ---------------------------------------------------------------------------
# HTTP dashboard (job queue / mobile tracker)
# EYE backend owns :8766 — JobPilot uses :8767 to avoid collision.
# ---------------------------------------------------------------------------
DEFAULT_SERVE_PORT: int = int(os.environ.get("JOBPILOT_SERVE_PORT", "8767"))

# ---------------------------------------------------------------------------
# Bro Client
# ---------------------------------------------------------------------------
BRO_BASE_URL: str = os.environ.get("BRO_URL", "http://127.0.0.1:8765")
TIMEOUT_CHAT: int = 120          # Ollama can be slow for complex queries
TIMEOUT_FAST: int = 30           # Fast model timeout
TIMEOUT_SHORT: int = 10          # Health / command timeout
MAX_RETRIES: int = 2
RETRY_DELAY: float = 1.0
HEALTH_CACHE_TTL: int = 5        # seconds

# ---------------------------------------------------------------------------
# Human-like Typing Simulation
# ---------------------------------------------------------------------------
TYPO_CHARS: str = "abcdefghijklmnopqrstuvwxyz"
MIN_DELAY_MS: int = 35
MAX_DELAY_MS: int = 130
TYPO_CHANCE: float = 0.04        # 4 % chance of a typo per character

# ---------------------------------------------------------------------------
# Field Filling
# ---------------------------------------------------------------------------
FILL_RETRIES: int = 3
FILL_RETRY_DELAY_MS: int = 500

# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------
WATCH_LOOP_INTERVAL: float = 1.0  # seconds between main-loop iterations

# ---------------------------------------------------------------------------
# Adzuna Job Search API
# ---------------------------------------------------------------------------
ADZUNA_APP_ID: str = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_API_KEY: str = os.environ.get("ADZUNA_API_KEY", "")
