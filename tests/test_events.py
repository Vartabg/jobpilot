"""
Tests for core/events.py — EventBus pub/sub.
"""

import pytest
from jobpilot.core.events import EventBus


class TestSubscribe:
    def test_on_registers_callback(self):
        bus = EventBus()
        bus.on("x", lambda **_: None)
        assert bus.listener_count == 1

    def test_on_deduplicates_same_callback(self):
        bus = EventBus()
        cb = lambda **_: None  # noqa: E731
        bus.on("x", cb)
        bus.on("x", cb)
        assert bus.listener_count == 1

    def test_off_removes_callback(self):
        bus = EventBus()
        cb = lambda **_: None  # noqa: E731
        bus.on("x", cb)
        bus.off("x", cb)
        assert bus.listener_count == 0

    def test_off_no_error_for_unknown(self):
        bus = EventBus()
        bus.off("x", lambda **_: None)  # should not raise


class TestEmit:
    def test_emit_calls_listener(self):
        bus = EventBus()
        results = []
        bus.on("ping", lambda **kw: results.append(kw))
        bus.emit("ping", value=42)
        assert results == [{"value": 42}]

    def test_emit_unknown_event_is_silent(self):
        bus = EventBus()
        bus.emit("nonexistent", foo="bar")  # should not raise

    def test_emit_multiple_listeners(self):
        bus = EventBus()
        a, b = [], []
        bus.on("x", lambda **kw: a.append(1))
        bus.on("x", lambda **kw: b.append(2))
        bus.emit("x")
        assert a == [1]
        assert b == [2]

    def test_emit_passes_kwargs(self):
        bus = EventBus()
        received = {}
        bus.on("e", lambda **kw: received.update(kw))
        bus.emit("e", label="Name", value="Alice")
        assert received == {"label": "Name", "value": "Alice"}


class TestClear:
    def test_clear_removes_all(self):
        bus = EventBus()
        bus.on("a", lambda **_: None)
        bus.on("b", lambda **_: None)
        assert bus.listener_count == 2
        bus.clear()
        assert bus.listener_count == 0


class TestIsolation:
    def test_separate_events_independent(self):
        bus = EventBus()
        a_results, b_results = [], []
        bus.on("a", lambda **_: a_results.append(1))
        bus.on("b", lambda **_: b_results.append(2))
        bus.emit("a")
        assert a_results == [1]
        assert b_results == []
