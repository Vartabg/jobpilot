"""
Tests for core/bro_client.py — HTTP client with mocked requests.
"""

import time
from unittest.mock import patch, MagicMock

import pytest
import requests

from jobpilot.core import bro_client


@pytest.fixture(autouse=True)
def _reset_health_cache():
    """Clear the module-level health cache before each test."""
    bro_client._health_cache["status"] = None
    bro_client._health_cache["timestamp"] = 0
    yield


# ---------------------------------------------------------------------------
# Health / connectivity
# ---------------------------------------------------------------------------

class TestGetHealth:
    @patch("jobpilot.core.bro_client.requests.get")
    def test_returns_json_on_200(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "ok", "whisper": "ready"},
        )
        result = bro_client.get_health()
        assert result["status"] == "ok"

    @patch("jobpilot.core.bro_client.requests.get")
    def test_returns_unreachable_on_exception(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError()
        result = bro_client.get_health()
        assert result["status"] == "unreachable"

    @patch("jobpilot.core.bro_client.requests.get")
    def test_caches_result(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "ok"},
        )
        bro_client.get_health()
        bro_client.get_health()
        assert mock_get.call_count == 1  # second call uses cache


class TestIsBroRunning:
    @patch("jobpilot.core.bro_client.get_health")
    def test_returns_true_when_ok(self, mock_health):
        mock_health.return_value = {"status": "ok"}
        assert bro_client.is_bro_running() is True

    @patch("jobpilot.core.bro_client.get_health")
    def test_returns_false_when_unreachable(self, mock_health):
        mock_health.return_value = {"status": "unreachable"}
        assert bro_client.is_bro_running() is False


class TestIsWhisperReady:
    @patch("jobpilot.core.bro_client.get_health")
    def test_ready_when_whisper_ready(self, mock_health):
        mock_health.return_value = {"whisper": "ready"}
        assert bro_client.is_whisper_ready() is True

    @patch("jobpilot.core.bro_client.get_health")
    def test_not_ready_when_missing(self, mock_health):
        mock_health.return_value = {}
        assert bro_client.is_whisper_ready() is False


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class TestChat:
    @patch("jobpilot.core.bro_client.requests.post")
    def test_returns_reply_on_200(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"reply": "Hello!"},
        )
        assert bro_client.chat("hi") == "Hello!"

    @patch("jobpilot.core.bro_client.requests.post")
    def test_returns_error_on_429(self, mock_post):
        mock_post.return_value = MagicMock(status_code=429)
        result = bro_client.chat("hi")
        assert "Rate limited" in result

    @patch("jobpilot.core.bro_client.requests.post")
    def test_returns_error_on_connection_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError()
        result = bro_client.chat("hi")
        assert "not running" in result

    @patch("jobpilot.core.bro_client.requests.post")
    def test_returns_error_on_timeout(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout()
        result = bro_client.chat("hi")
        assert "timed out" in result

    def test_empty_message_returns_empty(self):
        assert bro_client.chat("") == ""
        assert bro_client.chat("   ") == ""

    @patch("jobpilot.core.bro_client.requests.post")
    def test_prepends_context(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"reply": "ok"},
        )
        bro_client.chat("q", context="ctx")
        sent = mock_post.call_args[1]["json"]["message"]
        assert "ctx" in sent
        assert "q" in sent


# ---------------------------------------------------------------------------
# Speak
# ---------------------------------------------------------------------------

class TestSpeak:
    @patch("jobpilot.core.bro_client.requests.post")
    def test_returns_true_on_200(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        assert bro_client.speak("hello") is True

    @patch("jobpilot.core.bro_client.requests.post")
    def test_returns_false_on_exception(self, mock_post):
        mock_post.side_effect = Exception("boom")
        assert bro_client.speak("hello") is False

    def test_empty_text_returns_false(self):
        assert bro_client.speak("") is False


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------

class TestQueryRag:
    @patch("jobpilot.core.bro_client.requests.post")
    def test_returns_context_on_200(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"context": "resume data"},
        )
        assert bro_client.query_rag("python") == "resume data"

    @patch("jobpilot.core.bro_client.requests.post")
    def test_returns_empty_on_error(self, mock_post):
        mock_post.side_effect = Exception("fail")
        assert bro_client.query_rag("python") == ""


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

class TestRetryDecorator:
    def test_retries_on_connection_error(self):
        call_count = 0

        @bro_client._retry_on_failure(max_retries=2, delay=0.01)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise requests.exceptions.ConnectionError()
            return "ok"

        assert flaky() == "ok"
        assert call_count == 3

    def test_does_not_retry_on_other_errors(self):
        call_count = 0

        @bro_client._retry_on_failure(max_retries=2, delay=0.01)
        def bad():
            nonlocal call_count
            call_count += 1
            raise ValueError("nope")

        with pytest.raises(ValueError):
            bad()
        assert call_count == 1  # no retries
