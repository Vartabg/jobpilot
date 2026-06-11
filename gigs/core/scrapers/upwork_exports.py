"""Local Upwork PDF export ingestion.

This does not log in to Upwork or scrape behind an account. It only reads PDF
job posts that the user manually saved into the leads directory (default:
docs/revenue/upwork-leads; override with GIGPILOT_UPWORK_LEADS_DIR).
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from jobpilot.gigs.core.logger import get_logger
from jobpilot.gigs.core.models import Gig

log = get_logger(__name__)

DEFAULT_LEADS_DIR = Path("/Users/vartny/AI_Workspace/docs/revenue/upwork-leads")
UPWORK_URL_RE = re.compile(r"https://www\.upwork\.com/jobs/[^\s]+")
HOURLY_RANGE_RE = re.compile(r"\$(\d+(?:\.\d+)?)\s*-\s*\$(\d+(?:\.\d+)?)")

KEYWORD_TAGS = (
    "ai",
    "llm",
    "rag",
    "automation",
    "workflow",
    "agent",
    "n8n",
    "mcp",
    "openai",
    "claude",
    "anthropic",
    "python",
    "react",
    "chatbot",
    "vector",
    "retrieval",
    "healthcare",
    "crm",
)


def _leads_dir() -> Path:
    return Path(
        os.getenv("GIGPILOT_UPWORK_LEADS_DIR", str(DEFAULT_LEADS_DIR))
    ).expanduser()


def _pdf_text(path: Path) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", str(path), "-"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout
    except Exception as exc:
        log.warning("Upwork export parse failed for %s: %s", path.name, exc)
        return ""


def _title_from(path: Path, text: str) -> str:
    stem = path.stem
    if " - " in stem:
        return stem.rsplit(" - ", 1)[0].strip()

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines[:12]:
        if not line.startswith("4/") and line != "___" and "upwork.com" not in line:
            return line[:160]
    return stem[:160]


def _hourly_range(text: str) -> tuple[float, float]:
    matches = HOURLY_RANGE_RE.findall(text)
    if not matches:
        return 0.0, 0.0

    # Upwork hourly ranges are usually the last explicit dollar range before
    # the "Hourly" label. Use the highest pair to avoid catching old feedback.
    parsed = [(float(lo), float(hi)) for lo, hi in matches]
    return max(parsed, key=lambda pair: pair[1])


def _tags(text: str) -> list[str]:
    lower = text.lower()
    return [tag for tag in KEYWORD_TAGS if tag in lower]


def _stable_id(path: Path) -> str:
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
    return f"upwork-{digest}"


def scrape_upwork_exports() -> list[Gig]:
    """Read saved Upwork PDFs and return them as revenue leads."""
    directory = _leads_dir()
    if not directory.exists():
        log.info("Upwork exports dir missing: %s", directory)
        return []

    gigs: list[Gig] = []
    for path in sorted(directory.glob("*.pdf")):
        text = _pdf_text(path)
        if not text.strip():
            continue

        url_match = UPWORK_URL_RE.search(text)
        hourly_min, hourly_max = _hourly_range(text)
        gigs.append(
            Gig(
                id=_stable_id(path),
                source="upwork-export",
                title=_title_from(path, text),
                company="Upwork lead",
                url=url_match.group(0) if url_match else str(path),
                description=text[:1800],
                location="United States" if "United States" in text else "",
                pay_hourly_est=hourly_max or hourly_min,
                tags=_tags(text),
            )
        )

    log.info("Upwork exports: %d saved leads", len(gigs))
    return gigs
