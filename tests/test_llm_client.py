"""
Tests for core/llm_client.py — provider selection and Gemini REST handling,
with all network access mocked.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from jobpilot.core import llm_client
from jobpilot.core.bro_client import BroUnavailable


def _gemini_response(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json = lambda: payload if payload is not None else {}
    return response


def _gemini_text_payload(text: str) -> dict:
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": text}]},
                "finishReason": "STOP",
            }
        ]
    }


# ---------------------------------------------------------------------------
# Provider selection order
# ---------------------------------------------------------------------------

class TestProviderSelection:
    @patch("jobpilot.core.llm_client.is_bro_running", return_value=True)
    def test_bro_wins_when_reachable(self, _mock_bro, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key-123")
        assert llm_client.get_provider() == "bro"

    @patch("jobpilot.core.llm_client.is_bro_running", return_value=False)
    def test_gemini_when_bro_down_and_key_set(self, _mock_bro, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key-123")
        assert llm_client.get_provider() == "gemini"

    @patch("jobpilot.core.llm_client.is_bro_running", return_value=False)
    def test_none_when_no_backend(self, _mock_bro, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert llm_client.get_provider() is None
        assert llm_client.is_available() is False

    @patch("jobpilot.core.llm_client.is_bro_running", return_value=False)
    def test_blank_key_does_not_count(self, _mock_bro, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "   ")
        assert llm_client.get_provider() is None


# ---------------------------------------------------------------------------
# Bro path
# ---------------------------------------------------------------------------

class TestCompleteViaBro:
    @patch("jobpilot.core.llm_client.chat_or_raise", return_value="Three bullets.")
    @patch("jobpilot.core.llm_client.is_bro_running", return_value=True)
    def test_returns_bro_reply(self, _mock_bro, mock_chat):
        assert llm_client.complete("prompt", context="ctx", smart=True) == "Three bullets."
        mock_chat.assert_called_once_with("prompt", context="ctx", force_smart=True)

    @patch("jobpilot.core.llm_client.chat_or_raise", side_effect=BroUnavailable("Bro is busy."))
    @patch("jobpilot.core.llm_client.is_bro_running", return_value=True)
    def test_bro_failure_raises_typed_error(self, _mock_bro, _mock_chat):
        with pytest.raises(llm_client.LLMUnavailable):
            llm_client.complete("prompt")

    @patch("jobpilot.core.llm_client.chat_or_raise", return_value="   ")
    @patch("jobpilot.core.llm_client.is_bro_running", return_value=True)
    def test_empty_bro_reply_raises(self, _mock_bro, _mock_chat):
        with pytest.raises(llm_client.LLMUnavailable):
            llm_client.complete("prompt")


# ---------------------------------------------------------------------------
# Gemini path
# ---------------------------------------------------------------------------

@pytest.fixture
def gemini_env(monkeypatch):
    """Force the Gemini provider: Bro down, key set."""
    monkeypatch.setenv("GEMINI_API_KEY", "key-123")
    with patch("jobpilot.core.llm_client.is_bro_running", return_value=False):
        yield


class TestCompleteViaGemini:
    @patch("jobpilot.core.llm_client.requests.post")
    def test_happy_path_parses_text(self, mock_post, gemini_env):
        mock_post.return_value = _gemini_response(200, _gemini_text_payload("Tailored summary."))

        assert llm_client.complete("prompt") == "Tailored summary."

        url = mock_post.call_args[0][0]
        kwargs = mock_post.call_args[1]
        assert "generativelanguage.googleapis.com" in url
        assert llm_client.GEMINI_MODEL in url
        assert kwargs["params"] == {"key": "key-123"}
        assert kwargs["json"]["contents"][0]["parts"][0]["text"] == "prompt"

    @patch("jobpilot.core.llm_client.requests.post")
    def test_context_is_prepended(self, mock_post, gemini_env):
        mock_post.return_value = _gemini_response(200, _gemini_text_payload("ok"))

        llm_client.complete("prompt", context="resume facts")

        sent = mock_post.call_args[1]["json"]["contents"][0]["parts"][0]["text"]
        assert "resume facts" in sent
        assert "prompt" in sent

    @patch("jobpilot.core.llm_client.requests.post")
    def test_multiple_parts_are_joined(self, mock_post, gemini_env):
        payload = {
            "candidates": [
                {"content": {"parts": [{"text": "part one "}, {"text": "part two"}]}}
            ]
        }
        mock_post.return_value = _gemini_response(200, payload)
        assert llm_client.complete("prompt") == "part one part two"

    @patch("jobpilot.core.llm_client.requests.post")
    def test_non_200_raises_with_status(self, mock_post, gemini_env):
        mock_post.return_value = _gemini_response(
            403, {"error": {"message": "API key not valid"}}
        )
        with pytest.raises(llm_client.LLMUnavailable) as excinfo:
            llm_client.complete("prompt")
        assert "GEMINI_API_KEY" in str(excinfo.value)
        assert "API key not valid" in str(excinfo.value)

    @patch("jobpilot.core.llm_client.requests.post")
    def test_rate_limit_raises(self, mock_post, gemini_env):
        mock_post.return_value = _gemini_response(429, {})
        with pytest.raises(llm_client.LLMUnavailable) as excinfo:
            llm_client.complete("prompt")
        assert "429" in str(excinfo.value)

    @patch("jobpilot.core.llm_client.requests.post")
    def test_safety_block_raises(self, mock_post, gemini_env):
        mock_post.return_value = _gemini_response(
            200, {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
        )
        with pytest.raises(llm_client.LLMUnavailable) as excinfo:
            llm_client.complete("prompt")
        assert "SAFETY" in str(excinfo.value)

    @patch("jobpilot.core.llm_client.requests.post")
    def test_empty_candidates_raises(self, mock_post, gemini_env):
        mock_post.return_value = _gemini_response(200, {"candidates": []})
        with pytest.raises(llm_client.LLMUnavailable):
            llm_client.complete("prompt")

    @patch("jobpilot.core.llm_client.requests.post")
    def test_network_error_raises_typed_error(self, mock_post, gemini_env):
        mock_post.side_effect = requests.exceptions.ConnectionError("no route")
        with pytest.raises(llm_client.LLMUnavailable):
            llm_client.complete("prompt")

    @patch("jobpilot.core.llm_client.requests.post")
    def test_invalid_json_raises(self, mock_post, gemini_env):
        response = MagicMock()
        response.status_code = 200
        response.json.side_effect = ValueError("not json")
        mock_post.return_value = response
        with pytest.raises(llm_client.LLMUnavailable):
            llm_client.complete("prompt")


# ---------------------------------------------------------------------------
# No backend
# ---------------------------------------------------------------------------

class TestNoBackend:
    @patch("jobpilot.core.llm_client.is_bro_running", return_value=False)
    def test_complete_raises_with_setup_hint(self, _mock_bro, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(llm_client.LLMUnavailable) as excinfo:
            llm_client.complete("prompt")
        message = str(excinfo.value)
        assert "GEMINI_API_KEY" in message
        assert "aistudio.google.com" in message
