"""
Browser Interface — Protocol defining the surface area the engine needs.

Any class that implements these methods can be used as a browser backend:
- ``CDPBridge`` for real Chrome connections
- ``MockBrowser`` for testing without a browser

Uses ``typing.Protocol`` so CDPBridge satisfies the protocol implicitly
(structural subtyping) — no base-class changes needed.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from jobpilot.core.cdp_bridge import PageInfo


@runtime_checkable
class BrowserInterface(Protocol):
    """Structural protocol for browser backends."""

    @property
    def page(self) -> Any:
        """The underlying page object (Playwright Page or mock)."""
        ...

    async def connect(self) -> bool:
        """Connect to the browser. Returns True on success."""
        ...

    async def disconnect(self) -> None:
        """Disconnect and clean up resources."""
        ...

    async def get_page_info(self) -> PageInfo:
        """Return metadata about the current page."""
        ...

    async def get_active_page(self) -> Any:
        """Switch to and return the best page to work with."""
        ...

    async def inject_script(self, script: str) -> Any:
        """Execute JavaScript in the current page."""
        ...

    async def query_all(self, selector: str) -> list:
        """Query all elements matching *selector*."""
        ...
