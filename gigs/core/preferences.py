"""Single source of truth for identity + pay targets used across crib sheet,
email signoff, and salary-anchor logic.

Edit `data/preferences.json` to change values without touching code. If the
file is absent or partial, defaults below take over for missing keys.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobpilot.gigs.core.paths import data_dir
from typing import Any

DATA_DIR = data_dir()
PREFS_PATH = DATA_DIR / "preferences.json"

DEFAULTS: dict[str, Any] = {
    # Placeholder identity. Real values belong in data/preferences.json
    # (gitignored). These defaults exist only so the tool runs on a fresh
    # clone — they intentionally contain no personal information.
    "identity": {
        "first_name": "Your",
        "last_name": "Name",
        "email": "you@example.com",
        "phone": "000-000-0000",
        "phone_note": "(text preferred)",
        "linkedin": "https://www.linkedin.com/in/your-handle",
        "github": "https://github.com/your-handle",
        "portfolio": "https://your-portfolio.example.com",
        "city": "Your City, ST",
        "tagline": "Your professional tagline",
    },
    "pay": {
        "target_annual_usd": 175000,
        "target_hourly_usd": 90,
        "floor_annual_usd": 130000,
        "floor_hourly_usd": 65,
        "anchor_within_band_pct": 85,
    },
    # Outreach pages referenced in email drafts and prep packets. Placeholders
    # only — put your real URLs in data/preferences.json (gitignored).
    "links": {
        "service_page": "https://your-portfolio.example.com/services",
        "work_page": "https://your-portfolio.example.com/work",
    },
    # Lowercase substrings that mark a role as in your home metro (drives the
    # "currently located here" vs "willing to relocate" crib-sheet answer).
    # Empty by default — set in data/preferences.json, e.g. ["new york", "nyc"].
    "location": {
        "home_metro_tags": [],
    },
    "tailoring": {
        # Skills the user wants the email opener to call out when a gig
        # description mentions them. Order = priority for which to pick.
        "skill_keywords": [
            "three.js", "threejs", "react three fiber", "r3f", "webgpu",
            "rag", "retrieval-augmented", "agentic", "agent",
            "claude", "anthropic", "mcp", "model context protocol",
            "playwright", "browser-use", "chrome devtools",
            "next.js", "nextjs", "fastapi",
            "python", "typescript",
            "postgres", "sqlite",
            "vercel", "tailscale",
        ],
    },
    # Copy-paste sources for ATS essay questions ("Tell us about your
    # background", "Why this role"). Placeholders only — put your real bullets
    # in data/preferences.json (gitignored).
    "background_bullets": {
        "elevator_pitch": "One or two sentences on who you are and what you build.",
        "ai_agent_systems": "Describe an AI / agent system you've built.",
        "full_stack_web": "Describe a full-stack or web project you've shipped.",
        "browser_automation": "Describe relevant browser-automation or integration work.",
        "developer_tooling": "Describe developer tooling or infrastructure you've built.",
        "field_engineering": "Describe relevant prior engineering or domain experience.",
        "education": "Your education and how it informs your work.",
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: Path = PREFS_PATH) -> dict[str, Any]:
    """Read prefs from disk, falling back to DEFAULTS for missing keys."""
    if not path.exists():
        return DEFAULTS
    try:
        loaded = json.loads(path.read_text())
        if not isinstance(loaded, dict):
            return DEFAULTS
        return _deep_merge(DEFAULTS, loaded)
    except Exception:
        return DEFAULTS


def write_default_if_missing(path: Path = PREFS_PATH) -> bool:
    """Seed the preferences file with defaults if it doesn't exist yet."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULTS, indent=2))
    return True


def identity(prefs: dict[str, Any] | None = None) -> dict[str, str]:
    return (prefs or load())["identity"]


def pay(prefs: dict[str, Any] | None = None) -> dict[str, Any]:
    return (prefs or load())["pay"]


def links(prefs: dict[str, Any] | None = None) -> dict[str, str]:
    return dict((prefs or load())["links"])


def home_metro_tags(prefs: dict[str, Any] | None = None) -> list[str]:
    return list((prefs or load())["location"]["home_metro_tags"])


def skill_keywords(prefs: dict[str, Any] | None = None) -> list[str]:
    return list((prefs or load())["tailoring"]["skill_keywords"])


def background_bullets(prefs: dict[str, Any] | None = None) -> dict[str, str]:
    return dict((prefs or load())["background_bullets"])


def signoff_block(prefs: dict[str, Any] | None = None) -> str:
    """Render the standard email signoff using current identity preferences."""
    ident = identity(prefs)
    phone_part = ident["phone"]
    if ident.get("phone_note"):
        phone_part = f"{phone_part} {ident['phone_note']}"
    lines = [
        "Best,",
        f"{ident['first_name']} {ident['last_name']}",
        phone_part,
        f"{ident['linkedin']} | {ident['portfolio']}",
    ]
    return "\n".join(lines)
