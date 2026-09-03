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
    """Fake Playwright Page — url attribute + async query_selector/goto."""
    page = SimpleNamespace(url=url)
    page.query_selector = AsyncMock(
        return_value=object() if has_easy_apply_modal else None
    )
    page.goto = AsyncMock()
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


# ---------------------------------------------------------------------------
# neutralize_page — regression coverage for the 2026-06-03 Tread incident:
# a fill run was declined at the submit-confirm prompt, but the staged tab
# was left live in the browser and the application ended up submitted anyway.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_neutralize_page_matches_by_exact_url_among_other_tabs():
    """Must navigate the specific staged tab, not whatever get_active_page() would pick."""
    linkedin = _make_page("https://www.linkedin.com/feed/")
    staged = _make_page("https://jobs.ashbyhq.com/acme/apply/123")

    bridge = _bridge_with_pages([linkedin, staged])

    result = await bridge.neutralize_page("https://jobs.ashbyhq.com/acme/apply/123")

    assert result is True
    staged.goto.assert_awaited_once_with("about:blank", timeout=5000)
    linkedin.goto.assert_not_awaited()


@pytest.mark.asyncio
async def test_neutralize_page_falls_back_to_last_page_when_url_not_found():
    """A closed/renamed tab shouldn't leave the staged form live — fall back to self._page."""
    bridge = _bridge_with_pages([_make_page("https://example.com/")])
    fallback = _make_page("https://jobs.ashbyhq.com/acme/apply/123")
    bridge._page = fallback

    result = await bridge.neutralize_page("https://jobs.ashbyhq.com/apply/already-closed")

    assert result is True
    fallback.goto.assert_awaited_once_with("about:blank", timeout=5000)


@pytest.mark.asyncio
async def test_neutralize_page_returns_false_with_no_page_available():
    """No context and no last-known page — nothing to neutralize, must not raise."""
    bridge = CDPBridge()

    assert await bridge.neutralize_page("https://example.com/") is False


@pytest.mark.asyncio
async def test_neutralize_page_swallows_goto_failure():
    """A tab that's already closed must not blow up the caller — best-effort only."""
    staged = _make_page("https://jobs.ashbyhq.com/acme/apply/123")
    staged.goto = AsyncMock(side_effect=Exception("Target page, context or browser has been closed"))
    bridge = _bridge_with_pages([staged])

    result = await bridge.neutralize_page("https://jobs.ashbyhq.com/acme/apply/123")

    assert result is False


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
