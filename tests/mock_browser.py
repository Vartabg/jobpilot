"""
Mock Browser — in-memory browser double for testing.

Provides fake pages, elements, and overlays so that ``ApplicationEngine``
can be exercised end-to-end without launching Chrome.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from jobpilot.core.cdp_bridge import PageInfo


# ---------------------------------------------------------------------------
# Mock Element — mimics Playwright ElementHandle
# ---------------------------------------------------------------------------

class MockElement:
    """Lightweight stand-in for a Playwright ``ElementHandle``."""

    def __init__(
        self,
        *,
        tag: str = "input",
        input_type: str = "text",
        value: str = "",
        checked: bool = False,
        visible: bool = True,
        enabled: bool = True,
    ) -> None:
        self.tag = tag
        self.input_type = input_type
        self._value = value
        self._checked = checked
        self._visible = visible
        self._enabled = enabled
        self._pressed_keys: list[str] = []

    # -- Playwright-compatible async API ------------------------------------

    async def fill(self, value: str) -> None:
        self._value = value

    async def type(self, text: str, delay: int = 0) -> None:
        del delay  # delay is part of the Playwright API; the mock doesn't simulate timing.
        self._value += text

    async def press(self, key: str) -> None:
        if key == "Backspace":
            self._value = self._value[:-1]
        else:
            self._value += key
        self._pressed_keys.append(key)

    async def click(self) -> None:
        pass

    async def check(self) -> None:
        self._checked = True

    async def uncheck(self) -> None:
        self._checked = False

    async def is_checked(self) -> bool:
        return self._checked

    async def input_value(self) -> str:
        return self._value

    async def select_option(self, *, label: str = "", value: str = "") -> None:
        self._value = label or value

    async def set_input_files(self, path: str) -> None:
        self._value = path

    async def get_attribute(self, name: str) -> Optional[str]:
        attrs = {
            "type": self.input_type,
            "id": f"mock-{id(self)}",
        }
        return attrs.get(name)

    async def inner_text(self) -> str:
        return self._value

    async def wait_for_element_state(self, state: str, timeout: int = 2000) -> None:
        if state == "visible" and not self._visible:
            raise RuntimeError("Element not visible")
        if state == "enabled" and not self._enabled:
            raise RuntimeError("Element not enabled")

    async def query_selector(self, selector: str) -> Optional["MockElement"]:
        return None

    async def evaluate_handle(self, expression: str) -> "MockElement":
        return self

    async def evaluate(self, expression: str) -> Any:
        return None


# ---------------------------------------------------------------------------
# Mock Overlay / Chat — minimal stubs
# ---------------------------------------------------------------------------

class MockOverlay:
    """Stub for GhostOverlay."""

    def __init__(self) -> None:
        self.status: str = ""
        self.suggestions: list = []
        self.review_decision: str = "submit"
        self._pending_action: Optional[dict] = None

    async def inject(self) -> None:
        pass

    async def update_status(self, status: str) -> None:
        self.status = status

    async def show_suggestions(self, suggestions: list) -> None:
        self.suggestions = suggestions

    async def update_progress(self, **kwargs) -> None:
        pass

    async def show_review(self, fields: list) -> str:
        return self.review_decision

    async def get_pending_action(self) -> Optional[dict]:
        action = self._pending_action
        self._pending_action = None
        return action

    def queue_action(self, action: dict) -> None:
        """Test helper: queue an action to be returned by get_pending_action."""
        self._pending_action = action


class MockChat:
    """Stub for ChatOverlay."""

    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self._pending_messages: list[str] = []

    async def inject(self) -> None:
        pass

    async def ensure_injected(self) -> None:
        pass

    async def send_message(self, text: str) -> None:
        self.sent_messages.append(text)

    async def get_messages(self) -> list[str]:
        msgs = list(self._pending_messages)
        self._pending_messages.clear()
        return msgs

    def queue_message(self, msg: str) -> None:
        """Test helper: queue a user message."""
        self._pending_messages.append(msg)


# ---------------------------------------------------------------------------
# Mock Browser — satisfies BrowserInterface protocol
# ---------------------------------------------------------------------------

class MockBrowser:
    """In-memory browser backend for testing.

    Satisfies the ``BrowserInterface`` protocol.
    """

    def __init__(
        self,
        page_info: Optional[PageInfo] = None,
        page_sequence: Optional[list[PageInfo]] = None,
    ) -> None:
        self._page_info = page_info or PageInfo(
            url="https://www.linkedin.com/jobs/view/123",
            title="Senior Engineer - Easy Apply",
            is_linkedin=True,
            is_job_application=True,
            application_step=1,
        )
        self._page_sequence = list(page_sequence) if page_sequence else []
        self._seq_index = 0
        self._page = MockElement(tag="page")
        self._connected = False
        self._scripts: list[str] = []
        self._queries: list[str] = []

    @property
    def page(self) -> MockElement:
        return self._page

    async def connect(self) -> bool:
        self._connected = True
        return True

    async def disconnect(self) -> None:
        self._connected = False

    async def get_page_info(self) -> PageInfo:
        if self._page_sequence and self._seq_index < len(self._page_sequence):
            info = self._page_sequence[self._seq_index]
            self._seq_index += 1
            return info
        return self._page_info

    async def get_active_page(self) -> MockElement:
        return self._page

    async def inject_script(self, script: str) -> Any:
        self._scripts.append(script)
        return None

    async def query_all(self, selector: str) -> list:
        self._queries.append(selector)
        return []
