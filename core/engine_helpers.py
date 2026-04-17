"""
Engine helpers — standalone utility functions for the ApplicationEngine.

Pure functions and I/O helpers that don't depend on the engine instance.
"""
from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Optional

from jobpilot.core.config import (
    TYPO_CHARS, MIN_DELAY_MS, MAX_DELAY_MS, TYPO_CHANCE, SESSION_FILE,
)


# ---------------------------------------------------------------------------
# Human-like typing
# ---------------------------------------------------------------------------

async def _human_type(element, text: str) -> None:
    """Type text character-by-character with human-like timing.

    * Randomised inter-key delay (35-130 ms)
    * Occasional typo → backspace → correct character (~4 % per char)
    * Short pause after punctuation and spaces
    """
    for ch in text:
        if random.random() < TYPO_CHANCE:
            typo = random.choice(TYPO_CHARS)
            await element.type(typo, delay=random.randint(MIN_DELAY_MS, MAX_DELAY_MS))
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await element.press("Backspace")
            await asyncio.sleep(random.uniform(0.02, 0.08))

        delay = random.randint(MIN_DELAY_MS, MAX_DELAY_MS)
        if ch in " .,;:!?":
            delay += random.randint(20, 80)
        await element.type(ch, delay=delay)


async def _wait_for_stable(element, timeout_ms: int = 2000) -> bool:
    """Wait until an element is both visible and enabled (up to timeout)."""
    try:
        await element.wait_for_element_state("visible", timeout=timeout_ms)
        await element.wait_for_element_state("enabled", timeout=timeout_ms)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------

def _save_session(url: str, step: int, filled_fields: list[str]) -> None:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps({
        "url": url,
        "step": step,
        "filled_fields": filled_fields,
    }, indent=2))


def _load_session() -> Optional[dict]:
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text())
        except Exception:
            pass
    return None


def _clear_session() -> None:
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


# ---------------------------------------------------------------------------
# Context builder (pure function, no side effects)
# ---------------------------------------------------------------------------

def _build_job_context(page_info, app_page, parsed_jd=None) -> str:
    """Build rich context string for AI based on current application state."""
    parts = ["User is on LinkedIn."]

    if parsed_jd:
        parts.append(parsed_jd.summary(300))
    elif page_info and page_info.title:
        job_title = (
            page_info.title.replace("Easy Apply", "")
            .replace("|", "-")
            .strip()
        )
        parts.append(f"Job: {job_title}")

    if page_info and page_info.is_job_application:
        parts.append("They are in an Easy Apply form.")
        if app_page:
            parts.append(
                f"Step {app_page.current_step}/{app_page.total_steps}"
            )
            if app_page.fields:
                unfilled = [f for f in app_page.fields if not f.current_value]
                filled = [f for f in app_page.fields if f.current_value]
                if unfilled:
                    names = [
                        f.label or f.semantic_type.value
                        for f in unfilled[:3]
                    ]
                    parts.append(f"Unfilled: {', '.join(names)}")
                if filled:
                    parts.append(f"{len(filled)} fields already filled.")

    return " ".join(parts)
