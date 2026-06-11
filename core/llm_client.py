"""
Provider-agnostic LLM client for JobPilot.
==========================================
Single entry point for AI completions. Picks the first available backend:

1. **Bro** — an optional local AI server (see bro_client.py). Used
   automatically when it responds to a health check.
2. **Gemini** — Google's Gemini REST API, used when the GEMINI_API_KEY
   environment variable is set. Free-tier keys work:
   https://aistudio.google.com/app/apikey
3. **None** — complete() raises LLMUnavailable so callers can fall back to
   their template-based behavior. Errors are never returned as if they were
   AI-generated text.
"""

from __future__ import annotations

import os

import requests

from jobpilot.core.bro_client import BroUnavailable, chat_or_raise, is_bro_running
from jobpilot.core.config import TIMEOUT_CHAT
from jobpilot.core.logger import get_logger

log = get_logger(__name__)

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

NO_BACKEND_MESSAGE = (
    "AI features disabled — set GEMINI_API_KEY "
    "(free tier: https://aistudio.google.com/app/apikey)"
)


class LLMUnavailable(Exception):
    """No AI backend produced a completion.

    Raised when no backend is configured/reachable or the request failed.
    Callers should catch this and fall back to non-AI behavior — never show
    the error text as if it were generated content.
    """


def _gemini_api_key() -> str:
    return os.environ.get(GEMINI_API_KEY_ENV, "").strip()


def get_provider() -> str | None:
    """Return the active backend name: "bro", "gemini", or None."""
    if is_bro_running():
        return "bro"
    if _gemini_api_key():
        return "gemini"
    return None


def is_available() -> bool:
    """True when at least one AI backend can serve completions."""
    return get_provider() is not None


def complete(prompt: str, *, context: str | None = None, smart: bool = False) -> str:
    """Return a completion from the first available backend.

    Args:
        prompt: The instruction/question for the model.
        context: Optional extra context prepended to the prompt.
        smart: Hint that the request is complex (Bro routes it to its
            smarter/slower model; Gemini ignores it).

    Raises:
        LLMUnavailable: when no backend is available or the request fails.
    """
    provider = get_provider()

    if provider == "bro":
        try:
            reply = chat_or_raise(prompt, context=context, force_smart=smart)
        except BroUnavailable as exc:
            raise LLMUnavailable(f"Local AI backend failed: {exc}") from exc
        if reply.strip():
            return reply
        raise LLMUnavailable("Local AI backend returned an empty reply.")

    if provider == "gemini":
        return _complete_gemini(prompt, context=context)

    raise LLMUnavailable(NO_BACKEND_MESSAGE)


def _complete_gemini(prompt: str, *, context: str | None = None) -> str:
    """Call the Gemini REST API directly (no SDK dependency)."""
    api_key = _gemini_api_key()
    if not api_key:
        raise LLMUnavailable(NO_BACKEND_MESSAGE)

    full_prompt = f"{context}\n\n{prompt}" if context else prompt
    url = GEMINI_ENDPOINT.format(model=GEMINI_MODEL)

    try:
        response = requests.post(
            url,
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": full_prompt}]}]},
            timeout=TIMEOUT_CHAT,
        )
    except requests.exceptions.RequestException as exc:
        raise LLMUnavailable(f"Could not reach the Gemini API: {exc}") from exc

    if response.status_code != 200:
        raise LLMUnavailable(_gemini_error_message(response))

    try:
        payload = response.json()
    except ValueError as exc:
        raise LLMUnavailable("Gemini returned a response that was not valid JSON.") from exc

    return _parse_gemini_payload(payload)


def _parse_gemini_payload(payload: dict) -> str:
    """Extract candidates[0].content.parts[*].text, handling safety blocks."""
    candidates = payload.get("candidates") or []
    if not candidates:
        feedback = payload.get("promptFeedback") or {}
        reason = feedback.get("blockReason")
        if reason:
            raise LLMUnavailable(f"Gemini blocked the request (reason: {reason}).")
        raise LLMUnavailable("Gemini returned no completion candidates.")

    candidate = candidates[0] or {}
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(
        part.get("text", "") for part in parts if isinstance(part, dict)
    ).strip()

    if not text:
        finish_reason = candidate.get("finishReason", "")
        if finish_reason and finish_reason not in {"STOP", "MAX_TOKENS"}:
            raise LLMUnavailable(
                f"Gemini did not return text (finish reason: {finish_reason})."
            )
        raise LLMUnavailable("Gemini returned an empty completion.")

    return text


def _gemini_error_message(response: requests.Response) -> str:
    """Build a readable message for a non-200 Gemini response."""
    detail = ""
    try:
        detail = str((response.json().get("error") or {}).get("message") or "")
    except Exception:
        detail = ""

    if response.status_code in (401, 403):
        base = (
            f"Gemini rejected the API key (HTTP {response.status_code}) — "
            "check your GEMINI_API_KEY"
        )
    elif response.status_code == 429:
        base = "Gemini rate limit reached (HTTP 429) — try again in a minute"
    else:
        base = f"Gemini API error (HTTP {response.status_code})"

    return f"{base}: {detail}" if detail else base
