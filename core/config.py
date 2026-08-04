"""
Configuration — single source of truth for all JobPilot constants.

Every tunable value lives here.  Modules import from this file
instead of defining their own magic numbers.
"""

import os
from pathlib import Path

# Load .env if python-dotenv is available (dev convenience).
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).parent.parent / "data"
SESSION_FILE = DATA_DIR / "session.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
CHROME_PROFILE = Path(
    os.environ.get("JOBPILOT_CHROME_PROFILE", "~/.jobpilot-chrome-profile")
).expanduser()
CHROME_EXECUTABLE = os.environ.get("JOBPILOT_CHROME_EXECUTABLE", "").strip()
SYSTEM_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SYSTEM_CHROME_APP = Path("/Applications/Google Chrome.app")

# ---------------------------------------------------------------------------
# HTTP dashboard (job queue / mobile tracker)
# EYE backend owns :8766 — JobPilot uses :8767 to avoid collision.
# ---------------------------------------------------------------------------
DEFAULT_SERVE_PORT: int = int(os.environ.get("JOBPILOT_SERVE_PORT", "8767"))

# ---------------------------------------------------------------------------
# Bro Client
# ---------------------------------------------------------------------------
BRO_BASE_URL: str = os.environ.get("BRO_URL", "http://127.0.0.1:8765")
TIMEOUT_CHAT: int = 120  # Ollama can be slow for complex queries
TIMEOUT_FAST: int = 30  # Fast model timeout
TIMEOUT_SHORT: int = 10  # Health / command timeout
MAX_RETRIES: int = 2
RETRY_DELAY: float = 1.0
HEALTH_CACHE_TTL: int = 5  # seconds

# ---------------------------------------------------------------------------
# Optional AI providers
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
OLLAMA_MODEL: str = "codex-prime:latest"
OLLAMA_HEALTH_CACHE_TTL: int = 5


def get_gemini_api_key() -> str:
    """Return the configured Gemini key without exposing environment access elsewhere."""
    return os.environ.get("GEMINI_API_KEY", "").strip()


def get_ollama_base_url() -> str:
    """Return the opt-in Ollama endpoint."""
    return os.environ.get("OLLAMA_URL", OLLAMA_BASE_URL).rstrip("/")


def get_ollama_model() -> str:
    """Return the selected emergency Ollama model."""
    return os.environ.get("JOBPILOT_OLLAMA_MODEL", OLLAMA_MODEL).strip() or OLLAMA_MODEL


def is_ollama_enabled() -> bool:
    """Return whether the abandoned local-model fallback was explicitly enabled."""
    return os.environ.get("JOBPILOT_USE_OLLAMA", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


# ---------------------------------------------------------------------------
# Human-like Typing Simulation
# ---------------------------------------------------------------------------
TYPO_CHARS: str = "abcdefghijklmnopqrstuvwxyz"
MIN_DELAY_MS: int = 35
MAX_DELAY_MS: int = 130
TYPO_CHANCE: float = 0.04  # 4 % chance of a typo per character

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
