"""A LanguageModel served by Ollama on this computer.

Requests ask for JSON matching a schema, with thinking off and temperature 0,
so answers are compact and repeatable. Any failure raises ModelError. Note that
Ollama models whose names end in ":cloud" run on remote servers; ``is_local``
reports that so callers can warn.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

import requests

from jobpilot.engine.domain.fit import ModelError

DEFAULT_URL = "http://127.0.0.1:11434"
TIMEOUT_SECONDS = 300
_THINKING = re.compile(r"<think>.*?</think>", re.DOTALL)


class OllamaModel:
    def __init__(
        self,
        model: str,
        *,
        url: str = DEFAULT_URL,
        session: requests.Session | None = None,
    ) -> None:
        if not model:
            raise ValueError("an Ollama model name is required")
        self.model = model
        self.url = url.rstrip("/")
        self.session = session or requests.Session()

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    @property
    def is_local(self) -> bool:
        return not self.model.endswith(":cloud")

    def generate_json(self, prompt: str, *, schema: Mapping[str, Any]) -> Any:
        body = {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": dict(schema),
            "options": {"temperature": 0},
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            response = self.session.post(
                f"{self.url}/api/chat", json=body, timeout=TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            raise ModelError(
                f"Ollama isn't reachable at {self.url}: {exc.__class__.__name__}"
            ) from None
        if response.status_code != 200:
            raise ModelError(f"Ollama answered HTTP {response.status_code}")
        try:
            content = response.json()["message"]["content"]
            return json.loads(_THINKING.sub("", content).strip())
        except (ValueError, KeyError, TypeError) as exc:
            raise ModelError(
                f"Ollama's answer wasn't the JSON asked for ({exc.__class__.__name__})"
            ) from None
