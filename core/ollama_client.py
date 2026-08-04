"""Opt-in emergency Ollama backend for JobPilot."""

from __future__ import annotations

import time

import requests

from jobpilot.core.config import (
    OLLAMA_HEALTH_CACHE_TTL,
    TIMEOUT_CHAT,
    TIMEOUT_SHORT,
    get_ollama_base_url,
    get_ollama_model,
    is_ollama_enabled,
)

_health_cache: dict[str, object] = {
    "ok": False,
    "timestamp": 0.0,
    "ttl": OLLAMA_HEALTH_CACHE_TTL,
}


class OllamaUnavailable(Exception):
    """The optional Ollama backend could not provide a completion."""


def _model_matches(installed: str, wanted: str) -> bool:
    """Return whether model names match, treating an omitted tag as latest."""
    installed_base, _, installed_tag = installed.strip().lower().partition(":")
    wanted_base, _, wanted_tag = wanted.strip().lower().partition(":")
    return installed_base == wanted_base and (installed_tag or "latest") == (
        wanted_tag or "latest"
    )


def is_available() -> bool:
    """Return whether the explicitly enabled Ollama model is reachable."""
    if not is_ollama_enabled():
        return False
    now = time.time()
    if now - float(_health_cache["timestamp"]) < float(_health_cache["ttl"]):
        return bool(_health_cache["ok"])

    available = False
    try:
        response = requests.get(
            f"{get_ollama_base_url()}/api/tags",
            timeout=TIMEOUT_SHORT,
        )
        if response.status_code == 200:
            models = (response.json() or {}).get("models") or []
            names = [
                str(model.get("name") or model.get("model") or "")
                for model in models
                if isinstance(model, dict)
            ]
            wanted = get_ollama_model()
            available = any(_model_matches(name, wanted) for name in names if name)
    except requests.exceptions.RequestException:
        available = False

    _health_cache["ok"] = available
    _health_cache["timestamp"] = now
    return available


def complete(prompt: str, *, context: str | None = None) -> str:
    """Return one non-streaming completion from the selected Ollama model."""
    full_prompt = f"{context}\n\n{prompt}" if context else prompt
    model = get_ollama_model()
    base_url = get_ollama_base_url()

    try:
        response = requests.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": full_prompt}],
                "stream": False,
            },
            timeout=TIMEOUT_CHAT,
        )
    except requests.exceptions.RequestException as exc:
        _health_cache["timestamp"] = 0.0
        raise OllamaUnavailable(f"Could not reach Ollama at {base_url}: {exc}") from exc

    if response.status_code != 200:
        try:
            detail = str((response.json() or {}).get("error") or response.text[:200])
        except (TypeError, ValueError):
            detail = response.text[:200]
        suffix = f": {detail}" if detail else ""
        raise OllamaUnavailable(
            f"Ollama error (HTTP {response.status_code}) for model {model}{suffix}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise OllamaUnavailable(
            "Ollama returned a response that was not valid JSON."
        ) from exc

    text = ((payload.get("message") or {}).get("content") or "").strip()
    if not text:
        raise OllamaUnavailable(f"Ollama model {model} returned an empty completion.")
    return text
