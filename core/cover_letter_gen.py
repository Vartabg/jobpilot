"""
Cover Letter Generator — AI-tailored cover letters per application.

Generates a professional cover letter using:
  1. Parsed JD (from jd_parser) for company/role context
  2. Resume RAG context for candidate background (when the local Bro stack is up)
  3. The AI backend (local Bro or Gemini) for natural language generation

Cover letters are cached by JD hash to avoid re-generating for the same
posting. Saved as plain text in data/cover_letters/.
"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional
from jobpilot.core.logger import get_logger

log = get_logger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "cover_letters"


def _jd_hash(jd_text: str) -> str:
    """Deterministic short hash of a JD for cache keying."""
    return hashlib.sha256(jd_text.encode()).hexdigest()[:12]


def generate_cover_letter(
    jd_title: str,
    jd_company: str,
    jd_requirements: list[str],
    jd_raw_text: str,
    candidate_name: str = "",
    candidate_title: str = "",
) -> Optional[str]:
    """Generate a tailored cover letter.

    Returns the cover letter text, or None if generation fails.
    The result is also saved to data/cover_letters/{hash}.txt.
    """
    # Check cache first
    hash_key = _jd_hash(jd_raw_text)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = DATA_DIR / f"{hash_key}.txt"
    if cache_path.exists():
        log.info(f"Cover letter cache hit: {cache_path.name}")
        return cache_path.read_text()

    # Build prompt
    try:
        from jobpilot.core import llm_client
        from jobpilot.core.bro_client import is_bro_running, query_rag

        if not llm_client.is_available():
            log.warning("No AI backend available — cannot generate cover letter")
            return None

        # Pull resume context from RAG (only exists on the local Bro backend)
        resume_ctx = ""
        if is_bro_running():
            resume_ctx = query_rag(
                f"Summarise the candidate's experience relevant to {jd_title} at {jd_company}",
                top_k=5,
            )

        reqs_text = "\n".join(f"- {r}" for r in jd_requirements[:8]) if jd_requirements else "Not specified"

        prompt = f"""Write a professional, compelling cover letter for the following job application.
Keep it to 3 concise paragraphs. Use first person. Do NOT include placeholder brackets.

Job Title: {jd_title}
Company: {jd_company}
Key Requirements:
{reqs_text}

Candidate Background:
{resume_ctx or 'Experienced professional'}
{f'Name: {candidate_name}' if candidate_name else ''}
{f'Current Title: {candidate_title}' if candidate_title else ''}

Today's date: {datetime.now().strftime('%B %d, %Y')}

Begin the letter with "Dear Hiring Manager," and end with "Sincerely, {candidate_name or 'The Candidate'}".
"""

        try:
            letter = llm_client.complete(prompt, smart=True)
        except llm_client.LLMUnavailable as exc:
            log.warning(f"Cover letter generation failed: {exc}")
            return None

        if letter and len(letter) > 100:
            # Clean up
            letter = letter.strip()
            cache_path.write_text(letter)
            log.info(f"Generated cover letter ({len(letter)} chars) → {cache_path.name}")
            return letter

        log.warning("Cover letter generation returned insufficient text")
        return None

    except Exception as e:
        log.warning(f"Cover letter generation failed: {e}")
        return None


def get_cached_path(jd_raw_text: str) -> Optional[Path]:
    """Return the cached cover letter file path if it exists."""
    hash_key = _jd_hash(jd_raw_text)
    path = DATA_DIR / f"{hash_key}.txt"
    return path if path.exists() else None
