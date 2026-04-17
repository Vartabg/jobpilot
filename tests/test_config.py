"""
Tests for core/config.py — verify all constants are importable and sane.
"""

from pathlib import Path

from jobpilot.core.config import (
    DATA_DIR,
    SESSION_FILE,
    SETTINGS_FILE,
    BRO_BASE_URL,
    TIMEOUT_CHAT,
    TIMEOUT_FAST,
    TIMEOUT_SHORT,
    MAX_RETRIES,
    RETRY_DELAY,
    HEALTH_CACHE_TTL,
    TYPO_CHARS,
    MIN_DELAY_MS,
    MAX_DELAY_MS,
    TYPO_CHANCE,
    FILL_RETRIES,
    FILL_RETRY_DELAY_MS,
    WATCH_LOOP_INTERVAL,
)


class TestConfigPaths:
    def test_data_dir_is_path(self):
        assert isinstance(DATA_DIR, Path)

    def test_session_file_under_data_dir(self):
        assert SESSION_FILE.parent == DATA_DIR

    def test_settings_file_under_data_dir(self):
        assert SETTINGS_FILE.parent == DATA_DIR


class TestBroConstants:
    def test_base_url_is_string(self):
        assert isinstance(BRO_BASE_URL, str)
        assert BRO_BASE_URL.startswith("http")

    def test_timeouts_positive(self):
        assert TIMEOUT_CHAT > 0
        assert TIMEOUT_FAST > 0
        assert TIMEOUT_SHORT > 0

    def test_timeout_ordering(self):
        assert TIMEOUT_SHORT < TIMEOUT_FAST < TIMEOUT_CHAT

    def test_retry_params(self):
        assert MAX_RETRIES >= 0
        assert RETRY_DELAY > 0


class TestTypingSimulation:
    def test_delay_range_valid(self):
        assert 0 < MIN_DELAY_MS < MAX_DELAY_MS

    def test_typo_chance_in_range(self):
        assert 0.0 <= TYPO_CHANCE < 1.0

    def test_typo_chars_all_lowercase(self):
        assert TYPO_CHARS == TYPO_CHARS.lower()
        assert len(TYPO_CHARS) == 26


class TestFieldFilling:
    def test_fill_retries_positive(self):
        assert FILL_RETRIES > 0

    def test_retry_delay_positive(self):
        assert FILL_RETRY_DELAY_MS > 0


class TestPolling:
    def test_interval_positive(self):
        assert WATCH_LOOP_INTERVAL > 0
