"""Privacy hardening: API keys must not travel in URLs or land in logs."""

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from jobpilot.core import llm_client
from jobpilot.core.logger import RedactingFilter


def _gemini_response(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json = lambda: payload if payload is not None else {}
    return response


@pytest.fixture
def gemini_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "key-123-secret")
    with patch("jobpilot.core.llm_client.is_bro_running", return_value=False):
        yield


class TestGeminiKeyTransport:
    @patch("jobpilot.core.llm_client.requests.post")
    def test_key_sent_as_header_not_url_param(self, mock_post, gemini_env):
        mock_post.return_value = _gemini_response(
            200,
            {
                "candidates": [
                    {"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}
                ]
            },
        )
        assert llm_client.complete("prompt") == "ok"
        kwargs = mock_post.call_args[1]
        assert kwargs.get("params") in (None, {})
        assert kwargs["headers"]["x-goog-api-key"] == "key-123-secret"
        assert "key-123-secret" not in mock_post.call_args[0][0]

    @patch("jobpilot.core.llm_client.requests.post")
    def test_network_error_never_echoes_key(self, mock_post, gemini_env):
        mock_post.side_effect = requests.exceptions.ConnectionError(
            "failed: https://example.test/v1?key=key-123-secret"
        )
        with pytest.raises(llm_client.LLMUnavailable) as excinfo:
            llm_client.complete("prompt")
        assert "key-123-secret" not in str(excinfo.value)


class TestRedactingFilter:
    def _filtered(self, message: str) -> str:
        record = logging.LogRecord("t", logging.INFO, __file__, 1, message, (), None)
        assert RedactingFilter().filter(record) is True
        return record.getMessage()

    def test_redacts_emails_and_key_assignments(self):
        out = self._filtered("user garo@example.com GEMINI_API_KEY=key-123-secret done")
        assert "garo@example.com" not in out
        assert "key-123-secret" not in out

    def test_redacts_google_api_key_shape_and_bearer_tokens(self):
        google_key = "AIza" + "SyA1" + "x" * 31
        bearer_token = "abc" + ".def." + "ghi"
        out = self._filtered(f"{google_key} Bearer {bearer_token}")
        assert google_key not in out
        assert bearer_token not in out
