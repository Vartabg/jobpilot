"""
Event Bus — lightweight synchronous pub/sub for decoupling UI from logic.

Usage::

    bus = EventBus()
    bus.on("field_filled", lambda **kw: print(kw["label"]))
    bus.emit("field_filled", label="Email", value="a@b.com")

Designed to be dependency-free so that the engine never imports ``rich``
or any UI library directly.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable


class EventBus:
    """Simple synchronous event bus.

    * ``on(event, callback)``  — subscribe
    * ``off(event, callback)`` — unsubscribe
    * ``emit(event, **data)``  — fire all listeners for *event*

    Unknown events are silently ignored on ``emit``.
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable]] = defaultdict(list)

    # -- public API ----------------------------------------------------------

    def on(self, event: str, callback: Callable) -> None:
        """Register *callback* for *event*."""
        if callback not in self._listeners[event]:
            self._listeners[event].append(callback)

    def off(self, event: str, callback: Callable) -> None:
        """Remove *callback* from *event*."""
        try:
            self._listeners[event].remove(callback)
        except ValueError:
            pass

    def emit(self, event: str, **data) -> None:
        """Invoke every listener registered for *event*."""
        for cb in self._listeners.get(event, []):
            cb(**data)

    def clear(self) -> None:
        """Remove all listeners."""
        self._listeners.clear()

    @property
    def listener_count(self) -> int:
        """Total number of registered listener slots."""
        return sum(len(v) for v in self._listeners.values())


# ---------------------------------------------------------------------------
# Well-known event names (to avoid typos)
# ---------------------------------------------------------------------------
FIELD_FILLED = "field_filled"
FIELD_SKIPPED = "field_skipped"
FIELD_EDITED = "field_edited"
APPLICATION_STARTED = "application_started"
APPLICATION_SUBMITTED = "application_submitted"
APPLICATION_ABANDONED = "application_abandoned"
STATUS_UPDATE = "status_update"
INFO = "info"
WARNING = "warning"
ERROR = "error"
