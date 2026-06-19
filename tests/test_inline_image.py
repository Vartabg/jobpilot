"""Tests for the iTerm inline-image (imgcat / OSC 1337) emitter."""

import base64

from jobpilot.ui import inline_image


class _FakeStream:
    def __init__(self, tty: bool):
        self._tty = tty
        self.written = ""

    def isatty(self):
        return self._tty

    def write(self, s):
        self.written += s

    def flush(self):
        pass


def test_force_env_overrides_detection(monkeypatch):
    monkeypatch.setenv("JOBPILOT_FORCE_INLINE_IMAGES", "1")
    assert inline_image.supports_inline_images(_FakeStream(False)) is True
    monkeypatch.setenv("JOBPILOT_FORCE_INLINE_IMAGES", "0")
    assert inline_image.supports_inline_images(_FakeStream(True)) is False


def test_non_tty_has_no_inline_images(monkeypatch):
    monkeypatch.delenv("JOBPILOT_FORCE_INLINE_IMAGES", raising=False)
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    assert inline_image.supports_inline_images(_FakeStream(False)) is False


def test_iterm_tty_is_detected(monkeypatch):
    monkeypatch.delenv("JOBPILOT_FORCE_INLINE_IMAGES", raising=False)
    monkeypatch.delenv("LC_TERMINAL", raising=False)
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    assert inline_image.supports_inline_images(_FakeStream(True)) is True


def test_non_iterm_tty_is_not_detected(monkeypatch):
    monkeypatch.delenv("JOBPILOT_FORCE_INLINE_IMAGES", raising=False)
    monkeypatch.delenv("LC_TERMINAL", raising=False)
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    assert inline_image.supports_inline_images(_FakeStream(True)) is False


def test_format_carries_payload_and_markers(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    payload = b"\x89PNG\r\n\x1a\nhello"
    seq = inline_image.format_inline_image(payload, name="x.png")
    assert seq.startswith("\033]1337;File=")
    assert seq.endswith("\007")
    assert "inline=1" in seq
    assert f"size={len(payload)}" in seq
    assert base64.b64encode(payload).decode() in seq


def test_tmux_passthrough_wraps_sequence(monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-x,1,0")
    seq = inline_image.format_inline_image(b"abc")
    assert seq.startswith("\033Ptmux;")
    assert seq.endswith("\033\\")
    # inner ESC bytes are doubled for tmux
    assert "\033\033]1337" in seq


def test_print_inline_image_writes_to_stream(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    stream = _FakeStream(True)
    inline_image.print_inline_image(b"abc", stream=stream)
    assert "\033]1337;File=" in stream.written
    assert stream.written.endswith("\n")
