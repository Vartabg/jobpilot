"""
CDP Bridge - Connect to Chrome via DevTools Protocol

Uses Playwright to connect to an existing Chrome instance with remote debugging enabled.
This allows us to interact with pages while preserving the user's cookies/session.
"""

import asyncio
from dataclasses import dataclass
from typing import Optional
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

from jobpilot.core.logger import get_logger

log = get_logger(__name__)


@dataclass
class PageInfo:
    """Information about the current page state"""
    url: str
    title: str
    is_linkedin: bool
    is_job_application: bool
    application_step: Optional[int] = None


class CDPBridge:
    """
    Bridge to Chrome via Chrome DevTools Protocol.
    
    Connects to an existing Chrome instance launched with --remote-debugging-port.
    """
    
    def __init__(self, debug_port: int = 9222):
        self.debug_port = debug_port
        self.debug_url = f"http://localhost:{debug_port}"
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        
    async def connect(self) -> bool:
        """Connect to Chrome via CDP"""
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(self.debug_url)
            
            # Get the first context (user's default)
            contexts = self._browser.contexts
            if not contexts:
                log.error("No browser contexts found. Is Chrome open?")
                return False
                
            self._context = contexts[0]

            # Pick a usable page. Chrome exposes internal pages (chrome://,
            # devtools://, chrome-extension://) in context.pages — injecting
            # into those fails because their Trusted Types CSP blocks the
            # overlay's default policy workaround. Prefer LinkedIn / any http(s)
            # page; fall back to a new blank tab.
            self._page = await self.get_active_page()
            if self._page is None:
                self._page = await self._context.new_page()

            log.info("Connected to Chrome")
            log.info("Current page: %s", self._page.url)
            return True
            
        except Exception as e:
            log.error("Failed to connect: %s", e)
            log.error("Make sure Chrome is running with --remote-debugging-port=9222")
            log.error("Run: ./jobpilot/scripts/launch_chrome.sh")
            return False
    
    async def disconnect(self):
        """Disconnect from Chrome"""
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
            self._browser = None
            self._context = None
            self._page = None
    
    @property
    def page(self) -> Optional[Page]:
        """Get the current page"""
        return self._page
    
    async def get_page_info(self) -> PageInfo:
        """Get information about the current page"""
        if not self._page:
            raise RuntimeError("Not connected to browser")
            
        url = self._page.url
        title = await self._page.title()
        
        is_linkedin = "linkedin.com" in url
        is_job_application = is_linkedin and (
            "/jobs/view/" in url or 
            "jobs/collections" in url or
            "/jobs/search/" in url
        )
        
        # Detect application step if in Easy Apply modal
        application_step = None
        if is_job_application:
            # Check for Easy Apply modal
            step_indicator = await self._page.query_selector('[data-test-modal-id="easy-apply-modal"]')
            if step_indicator:
                # Try to get step number from progress indicator
                progress = await self._page.query_selector('.jobs-easy-apply-content progress')
                if progress:
                    value = await progress.get_attribute("value")
                    application_step = int(value) if value else 1
        
        return PageInfo(
            url=url,
            title=title,
            is_linkedin=is_linkedin,
            is_job_application=is_job_application,
            application_step=application_step
        )
    
    async def wait_for_navigation(self, timeout: float = 30.0):
        """Wait for page navigation"""
        if self._page:
            await self._page.wait_for_load_state("networkidle", timeout=timeout * 1000)
    
    async def get_active_page(self) -> Optional[Page]:
        """Get the best page to work with.

        Priority:
        1. LinkedIn page with an open Easy Apply modal
        2. Any LinkedIn page
        3. First injectable (http/https) page — skips chrome://, devtools://,
           chrome-extension://, about: URLs where overlay injection fails.
        """
        if not self._context:
            return None

        pages = self._context.pages
        if not pages:
            return None

        def _is_injectable(url: str) -> bool:
            return url.startswith("http://") or url.startswith("https://")

        # Priority 1: LinkedIn page with Easy Apply modal open
        for page in pages:
            if "linkedin.com" not in page.url:
                continue
            try:
                modal = await page.query_selector(
                    '[role="dialog"][aria-label*="Easy Apply"], '
                    '[data-test-modal-id="easy-apply-modal"], '
                    '.jobs-easy-apply-modal'
                )
                if modal:
                    if self._page != page:
                        log.debug("Switched to tab with Easy Apply modal")
                    self._page = page
                    return page
            except Exception:
                continue

        # Priority 2: Any LinkedIn page
        for page in pages:
            if "linkedin.com" in page.url:
                self._page = page
                return page

        # Priority 3: first http(s) page (skip chrome:// and friends)
        for page in pages:
            if _is_injectable(page.url):
                self._page = page
                return page

        return None
    
    async def inject_script(self, script: str) -> any:
        """Inject and execute JavaScript in the page"""
        if not self._page:
            raise RuntimeError("Not connected to browser")
        return await self._page.evaluate(script)
    
    async def query_all(self, selector: str) -> list:
        """Query all elements matching a selector"""
        if not self._page:
            return []
        return await self._page.query_selector_all(selector)


# Convenience function for one-off connections
async def connect_to_chrome(debug_port: int = 9222) -> Optional[CDPBridge]:
    """Connect to Chrome and return the bridge, or None on failure"""
    bridge = CDPBridge(debug_port)
    if await bridge.connect():
        return bridge
    return None
