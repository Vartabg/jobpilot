"""
Configuration — single source of truth for all JobPilot constants.

Every tunable value lives here.  Modules import from this file
instead of defining their own magic numbers.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).parent.parent / "data"
SESSION_FILE = DATA_DIR / "session.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

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
