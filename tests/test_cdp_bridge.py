"""Tests for tab-selection hardening in `core/cdp_bridge.py`.

Regression coverage for the 2026-04-16 incident where JobPilot attached to
`chrome://omnibox-popup.top-chrome/...` and overlay injection failed because
Chrome-internal pages enforce Trusted Types CSP the default-policy workaround
cannot bypass.

Also covers the open-then-immediate-close bug: disconnect() must not kill a
CDP-attached Chrome window (doctor / pilot boot used to flash-close it).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jobpilot.core.cdp_bridge import CDPBridge


def _make_page(url: str, *, has_easy_apply_modal: bool = False):
    """Fake Playwright Page — url attribute + async query_selector."""
    page = SimpleNamespace(url=url)
    page.query_selector = AsyncMock(
        return_value=object() if has_easy_apply_modal else None
    )
    return page


def _bridge_with_pages(pages):
    bridge = CDPBridge()
    bridge._context = SimpleNamespace(pages=pages)
    return bridge


@pytest.mark.asyncio
async def test_get_active_page_skips_chrome_internal_pages():
    """Priority-3 fallback must skip chrome://, devtools://, chrome-extension://."""
    chrome_internal = _make_page(
        "chrome://omnibox-popup.top-chrome/omnibox_popup_aim.html"
    )
    devtools = _make_page("devtools://devtools/bundled/inspector.html")
    user_tab = _make_page("https://example.com/")

    bridge = _bridge_with_pages([chrome_internal, devtools, user_tab])

    picked = await bridge.get_active_page()
    assert picked is user_tab


@pytest.mark.asyncio
async def test_get_active_page_prefers_linkedin_over_other_http_tabs():
    """Priority 2 must beat Priority 3 even when a chrome:// page is present."""
    chrome_internal = _make_page("chrome://new-tab-page/")
    other_site = _make_page("https://example.com/")
    linkedin = _make_page("https://www.linkedin.com/feed/")

    bridge = _bridge_with_pages([chrome_internal, other_site, linkedin])

    picked = await bridge.get_active_page()
    assert picked is linkedin


@pytest.mark.asyncio
async def test_get_active_page_prefers_easy_apply_modal_tab():
    """Priority 1: a LinkedIn tab with an Easy Apply modal wins over a plain feed tab."""
    feed = _make_page("https://www.linkedin.com/feed/")
    job_with_modal = _make_page(
        "https://www.linkedin.com/jobs/view/123", has_easy_apply_modal=True
    )

    bridge = _bridge_with_pages([feed, job_with_modal])

    picked = await bridge.get_active_page()
    assert picked is job_with_modal


@pytest.mark.asyncio
async def test_get_active_page_returns_none_when_only_chrome_internal_pages():
    """When every tab is a chrome:// internal page, caller must know to create a new tab."""
    bridge = _bridge_with_pages(
        [
            _make_page("chrome://omnibox-popup.top-chrome/omnibox_popup_aim.html"),
            _make_page("about:blank"),
        ]
    )

    assert await bridge.get_active_page() is None


@pytest.mark.asyncio
async def test_disconnect_detaches_cdp_without_closing_owned_context_by_default():
    """Default disconnect must not context.close() — that killed the window."""
    bridge = CDPBridge()
    context = AsyncMock()
    browser = AsyncMock()
    playwright = AsyncMock()

    bridge._context = context
    bridge._browser = browser
    bridge._playwright = playwright
    bridge._owns_browser_process = False

    await bridge.disconnect()

    context.close.assert_not_called()
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()
    assert bridge._browser is None
    assert bridge._playwright is None


@pytest.mark.asyncio
async def test_disconnect_close_browser_only_when_requested_and_owned():
    """Explicit close_browser=True may close a Playwright-owned context."""
    bridge = CDPBridge()
    context = AsyncMock()
    playwright = AsyncMock()

    bridge._context = context
    bridge._browser = None  # launch_persistent path: no CDP browser handle
    bridge._playwright = playwright
    bridge._owns_browser_process = True

    await bridge.disconnect(close_browser=True)

    context.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()
