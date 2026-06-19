"""Tests for terminal key helpers."""

from unittest.mock import patch

from jobpilot.ui.terminal_keys import osc8_link, read_key


def test_osc8_link_escapes():
    out = osc8_link("https://example.com?q=1", "example")
    assert "[link=https://example.com?q=1]example[/link]" == out


def test_osc8_link_escapes_brackets():
    out = osc8_link("https://x.com/a]b", "go")
    assert r"\]" in out or "\\]" in out


def test_read_key_not_tty():
    with patch("jobpilot.ui.terminal_keys.sys.stdin.isatty", return_value=False):
        assert read_key() is None


def test_read_key_single_char():
    with patch("jobpilot.ui.terminal_keys.sys.stdin.isatty", return_value=True):
        with patch("jobpilot.ui.terminal_keys.select.select", return_value=([object()], [], [])):
            with patch("jobpilot.ui.terminal_keys.sys.stdin.read", return_value="j"):
                assert read_key() == "j"


def test_read_key_arrow_down_csi():
    with patch("jobpilot.ui.terminal_keys.sys.stdin.isatty", return_value=True):
        with patch("jobpilot.ui.terminal_keys.select.select", side_effect=[
            ([object()], [], []),
            ([object()], [], []),
            ([object()], [], []),
        ]):
            with patch("jobpilot.ui.terminal_keys.sys.stdin.read", side_effect=["\x1b", "[", "B"]):
                assert read_key() == "down"


def test_read_key_arrow_down_ss3_iterm():
    """iTerm often sends SS3 \\eOB for arrow-down, not CSI \\e[B."""
    with patch("jobpilot.ui.terminal_keys.sys.stdin.isatty", return_value=True):
        with patch("jobpilot.ui.terminal_keys.select.select", side_effect=[
            ([object()], [], []),
            ([object()], [], []),
            ([object()], [], []),
        ]):
            with patch("jobpilot.ui.terminal_keys.sys.stdin.read", side_effect=["\x1b", "O", "B"]):
                assert read_key() == "down"


def test_read_key_arrow_down_modified_csi():
    with patch("jobpilot.ui.terminal_keys.sys.stdin.isatty", return_value=True):
        with patch("jobpilot.ui.terminal_keys.select.select", side_effect=[
            ([object()], [], []),
            ([object()], [], []),
            ([object()], [], []),
            ([object()], [], []),
            ([object()], [], []),
            ([object()], [], []),
        ]):
            with patch(
                "jobpilot.ui.terminal_keys.sys.stdin.read",
                side_effect=["\x1b", "[", "1", ";", "2", "B"],
            ):
                assert read_key() == "down"