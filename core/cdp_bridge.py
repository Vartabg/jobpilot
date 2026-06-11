"""
CDP Bridge - Manages a dedicated Playwright browser for JobPilot.

On connect():
  1. If a debug Chrome is already running on the port, reuse it (CDP).
  2. Otherwise, launch a persistent Chromium window with a dedicated profile
     so LinkedIn login is preserved across runs.
"""

from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

from jobpilot.core.logger import get_logger
from jobpilot.core.page_info import PageInfo, build_page_info

log = get_logger(__name__)

_PROFILE_DIR = Path.home() / ".jobpilot-chrome-profile"
_LINKEDIN_JOBS = "https://www.linkedin.com/jobs/"


class CDPBridge:
    """Manages a single dedicated browser window for job applications."""

    def __init__(self, debug_port: int = 9222):
        self.debug_port = debug_port
        self.debug_url = f"http://127.0.0.1:{debug_port}"
        self._playwright = None
        self._browser: Optional[Browser] = None  # set only for CDP reconnect path
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def connect(self) -> bool:
        """Connect to or launch the dedicated browser window."""
        self._playwright = await async_playwright().start()

        # --- path 1: reuse an already-running debug session ---
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(self.debug_url)
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
                self._page = await self.get_active_page()
                if self._page is None:
                    self._page = await self._context.new_page()
                log.info("Reconnected to existing session")
                log.info("Current page: %s", self._page.url)
                return True
        except Exception:
            self._browser = None  # fall through to launch

        # --- path 2: launch a fresh persistent window ---
        try:
            _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(_PROFILE_DIR),
                headless=False,
                args=[
                    f"--remote-debugging-port={self.debug_port}",
                    "--remote-allow-origins=*",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            # Reuse an existing LinkedIn tab or navigate the first page there
            self._page = await self.get_active_page()
            if self._page is None:
                pages = self._context.pages
                self._page = pages[0] if pages else await self._context.new_page()
                await self._page.goto(_LINKEDIN_JOBS)
            elif "/jobs/" not in self._page.url:
                # get_active_page() may return feed/notifications/messaging — force to jobs
                await self._page.goto(_LINKEDIN_JOBS)

            log.info("Launched dedicated browser window")
            log.info("Current page: %s", self._page.url)
            return True

        except Exception as e:
            log.error("Failed to launch browser: %s", e)
            return False

    async def disconnect(self):
        """Shut down gracefully. Closes the window only if we launched it."""
        if self._playwright:
            if self._context and not self._browser:
                # We own the persistent context — close the window
                await self._context.close()
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
        return await build_page_info(self._page)

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
