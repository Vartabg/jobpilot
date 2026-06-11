"""
Bro Integration Client
======================
Transport for the optional local "Bro" AI backend (Ollama + RAG + Whisper STT).

Most code should not call this module directly for completions — use
``jobpilot.core.llm_client``, which picks Bro when it is reachable and falls
back to the Gemini API when GEMINI_API_KEY is set.

Features:
- Intelligent model routing (fast for simple, smart for complex)
- Local STT via whisper.cpp
- Automatic retries with exponential backoff on connection errors
- Graceful degradation when Bro unavailable
"""

import requests
import time
from typing import Optional
from pathlib import Path
from functools import wraps

from jobpilot.core.config import (
    BRO_BASE_URL,
    TIMEOUT_CHAT,
    TIMEOUT_FAST,
    TIMEOUT_SHORT,
    MAX_RETRIES,
    RETRY_DELAY,
    HEALTH_CACHE_TTL,
)

# Cache for health status (avoid hammering /health)
_health_cache: dict[str, object] = {"status": None, "timestamp": 0, "ttl": HEALTH_CACHE_TTL}


class BroUnavailable(Exception):
    """Raised when Bro cannot produce a reply (down, busy, or errored)."""


def _retry_on_failure(max_retries: int = MAX_RETRIES, delay: float = RETRY_DELAY):
    """Decorator for automatic retry with exponential backoff."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except requests.exceptions.ConnectionError as e:
                    last_error = e
                    if attempt < max_retries:
                        time.sleep(delay * (2 ** attempt))
                except Exception as e:
                    last_error = e
                    break  # Don't retry on non-connection errors
            raise last_error if last_error else Exception("Unknown error")
        return wrapper
    return decorator


def get_health() -> dict[str, object]:
    """Get Bro health status (cached for 5 seconds)."""
    now = time.time()
    if _health_cache["status"] and now - _health_cache["timestamp"] < _health_cache["ttl"]:
        return _health_cache["status"]
    
    try:
        r = requests.get(f"{BRO_BASE_URL}/health", timeout=3)
        if r.status_code == 200:
            status = r.json()
            _health_cache["status"] = status
            _health_cache["timestamp"] = now
            return status
    except Exception:
        pass
    
    return {"status": "unreachable", "ollama": "unknown", "whisper": "unknown"}


def is_bro_running() -> bool:
    """Check if Bro server is reachable."""
    return get_health().get("status") == "ok"


def is_whisper_ready() -> bool:
    """Check if local STT (whisper.cpp) is available."""
    return get_health().get("whisper") == "ready"


def is_fast_model_available() -> bool:
    """Check if the fast model is loaded in Ollama."""
    health = get_health()
    fast_model = health.get("fast_model", "mistral")
    models = health.get("ollama_models", [])
    return any(fast_model in m for m in models)


@_retry_on_failure(max_retries=1)
def _post_with_retry(path: str, **kwargs) -> requests.Response:
    """POST to a Bro endpoint, retrying once on connection errors.

    Raises the underlying requests exception so the retry decorator can see
    ConnectionError (callers convert exceptions to their own contract).
    """
    return requests.post(f"{BRO_BASE_URL}{path}", **kwargs)


def chat_or_raise(message: str, context: Optional[str] = None, force_smart: bool = False) -> str:
    """
    Send a message to Bro's /chat endpoint (Ollama + RAG).

    Args:
        message: User message
        context: Optional extra context to prepend
        force_smart: Force use of the smart model (for complex questions)

    Returns:
        AI response string

    Raises:
        BroUnavailable: when Bro is down, busy, or returns an error.
    """
    if not message or not message.strip():
        return ""

    full_message = message
    if context:
        full_message = f"{context}\n\nUser question: {message}"

    # Estimate timeout based on message complexity
    timeout = TIMEOUT_CHAT if force_smart or len(full_message) > 500 else TIMEOUT_FAST

    try:
        r = _post_with_retry("/chat", json={"message": full_message}, timeout=timeout)
    except requests.exceptions.ConnectionError as e:
        raise BroUnavailable("Bro is not running.") from e
    except requests.exceptions.Timeout as e:
        raise BroUnavailable("Request timed out. Ollama may be busy.") from e
    except Exception as e:
        raise BroUnavailable(f"Error: {e}") from e

    if r.status_code == 200:
        return r.json().get("reply", "No response from model.")
    if r.status_code == 429:
        raise BroUnavailable("Rate limited. Try again in a moment.")
    if r.status_code == 503:
        raise BroUnavailable("Bro is busy. Try again.")
    raise BroUnavailable(f"Error: {r.status_code}")


def chat(message: str, context: Optional[str] = None, force_smart: bool = False) -> str:
    """
    Like chat_or_raise(), but returns the error message as a string instead of
    raising — the legacy contract some callers still rely on. New code should
    go through jobpilot.core.llm_client, which surfaces typed errors.
    """
    try:
        return chat_or_raise(message, context=context, force_smart=force_smart)
    except BroUnavailable as e:
        return str(e)


def speak(text: str) -> bool:
    """
    Send text to Bro's /speak endpoint for TTS.
    Returns True if successful.
    """
    if not text or not text.strip():
        return False
    
    try:
        r = requests.post(
            f"{BRO_BASE_URL}/speak",
            json={"text": text[:5000]},
            timeout=TIMEOUT_SHORT
        )
        return r.status_code == 200
    except Exception:
        return False


def send_command(command: str, args: Optional[dict] = None) -> dict:
    """
    Send a command to Bro's /jobpilot/command endpoint.
    Used for voice → JobPilot control.
    """
    try:
        r = requests.post(
            f"{BRO_BASE_URL}/jobpilot/command",
            json={"command": command, "args": args or {}},
            timeout=TIMEOUT_SHORT
        )
        return r.json() if r.status_code == 200 else {"error": r.status_code}
    except Exception as e:
        return {"error": str(e)}


def get_pending_commands() -> list[dict]:
    """
    Poll Bro for pending JobPilot commands (from voice).
    Returns list of {command, args} dicts.
    """
    try:
        r = requests.get(
            f"{BRO_BASE_URL}/jobpilot/commands",
            timeout=TIMEOUT_SHORT
        )
        if r.status_code == 200:
            return r.json().get("commands", [])
        return []
    except Exception:
        return []


def query_rag(query: str, top_k: int = 5) -> str:
    """
    Query Bro's RAG for relevant context (e.g., resume chunks).
    Returns formatted context string.
    """
    try:
        r = _post_with_retry(
            "/jobpilot/rag",
            json={"query": query, "top_k": top_k},
            timeout=TIMEOUT_SHORT
        )
        if r.status_code == 200:
            return r.json().get("context", "")
        return ""
    except Exception:
        return ""


def transcribe(audio_path: Path) -> str:
    """
    Transcribe audio file using Bro's local whisper.cpp STT.
    
    Args:
        audio_path: Path to audio file (wav, mp3, m4a, webm, ogg)
        
    Returns:
        Transcribed text, or error message
    """
    if not audio_path.exists():
        return "[Error: audio file not found]"
    
    if not is_whisper_ready():
        return "[STT unavailable: whisper not ready]"
    
    try:
        with open(audio_path, "rb") as f:
            r = requests.post(
                f"{BRO_BASE_URL}/transcribe",
                files={"audio": (audio_path.name, f, "audio/wav")},
                timeout=60
            )
        
        if r.status_code == 200:
            return r.json().get("text", "")
        elif r.status_code == 503:
            return "[STT unavailable]"
        else:
            return f"[STT error: {r.status_code}]"
    except requests.exceptions.Timeout:
        return "[STT timeout]"
    except Exception as e:
        return f"[STT error: {e}]"


def transcribe_bytes(audio_bytes: bytes, filename: str = "audio.webm") -> str:
    """
    Transcribe raw audio bytes using Bro's local whisper.cpp STT.
    
    Args:
        audio_bytes: Raw audio data
        filename: Filename hint for format detection
        
    Returns:
        Transcribed text, or error message
    """
    if not audio_bytes:
        return ""
    
    if not is_whisper_ready():
        return "[STT unavailable: whisper not ready]"
    
    try:
        r = requests.post(
            f"{BRO_BASE_URL}/transcribe",
            files={"audio": (filename, audio_bytes, "audio/webm")},
            timeout=60
        )
        
        if r.status_code == 200:
            return r.json().get("text", "")
        elif r.status_code == 503:
            return "[STT unavailable]"
        else:
            return f"[STT error: {r.status_code}]"
    except requests.exceptions.Timeout:
        return "[STT timeout]"
    except Exception as e:
        return f"[STT error: {e}]"
