"""Attach Playwright to JobPilot's long-lived, dedicated Chrome process."""

from __future__ import annotations

import asyncio
from typing import Any

from jobpilot.core.chrome_runtime import (
    CDP_READY_TIMEOUT_S,
    LINKEDIN_JOBS,
    cdp_ready,
    spawn_debug_chrome,
    wait_for_cdp,
)
from jobpilot.core.config import CHROME_PROFILE
from jobpilot.core.logger import get_logger
from jobpilot.core.page_info import PageInfo, build_page_info
from jobpilot.core.tab_selection import select_active_page
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

log = get_logger(__name__)


class CDPBridge:
    """Manage one CDP client; disconnecting never quits external Chrome."""

    def __init__(self, debug_port: int = 9222):
        self.debug_port = debug_port
        self.debug_url = f"http://127.0.0.1:{debug_port}"
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._owns_browser_process = False

    async def connect(self) -> bool:
        """Attach to existing CDP or launch detached Chrome and then attach."""
        self._playwright = await async_playwright().start()
<<<<<<< HEAD

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
            launch_options = {
                "headless": False,
                "args": [
                    f"--remote-debugging-port={self.debug_port}",
                    # No --remote-allow-origins: Playwright sends no Origin header,
                    # and the wildcard would let any website attach to the CDP socket.
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            }
            executable = _chrome_executable()
            if executable:
                launch_options["executable_path"] = executable
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(_PROFILE_DIR),
                **launch_options,
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
=======
        if await self._try_cdp_connect():
>>>>>>> origin/codex/jobpilot-cdp-runtime
            return True

        log.info("[CDP] No listener → launching detached Chrome on %s", self.debug_port)
        spawned = await asyncio.to_thread(
            spawn_debug_chrome,
            self.debug_port,
            CHROME_PROFILE,
        )
        if not spawned:
            await self._stop_playwright()
            return False
        if not await asyncio.to_thread(wait_for_cdp, self.debug_url):
            log.error(
                "[CDP] Chrome launch timed out → %s after %.0fs",
                self.debug_url,
                CDP_READY_TIMEOUT_S,
            )
            await self._stop_playwright()
            return False
        if await self._try_cdp_connect():
            log.info("[CDP] Detached Chrome connected → survives disconnect")
            return True
        log.error("[CDP] Chrome ready but attach failed → %s", self.debug_url)
        await self._stop_playwright()
        return False

    async def _try_cdp_connect(self) -> bool:
        if not cdp_ready(self.debug_url, timeout=0.8):
            return False
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(
                self.debug_url,
            )
            contexts = self._browser.contexts
            if not contexts:
                log.warning("[CDP] Connection incomplete → no browser context")
                return False
            self._context = contexts[0]
            self._page = await self.get_active_page()
            if self._page is None:
                self._page = await self._context.new_page()
                await self._page.goto(LINKEDIN_JOBS)
            elif "linkedin.com" not in self._page.url:
                await self._page.goto(LINKEDIN_JOBS)
            self._owns_browser_process = False
            log.info("[CDP] Existing session connected → %s", self._page.url)
            return True
        except Exception as exc:
            log.debug("[CDP] Attach failed → %s", exc)
            self._browser = None
            self._context = None
            self._page = None
            return False

    async def disconnect(self, *, close_browser: bool = False) -> None:
        """Detach; only a legacy Playwright-owned context may be explicitly closed."""
        if not self._playwright:
            return
        try:
            if close_browser and self._owns_browser_process and self._context:
                await self._context.close()
            elif self._browser is not None:
                await self._browser.close()
        except Exception as exc:
            log.debug("[CDP] Disconnect cleanup failed → %s", exc)
        finally:
            await self._stop_playwright()

    async def _stop_playwright(self) -> None:
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as exc:
                log.debug("[CDP] Playwright stop failed → %s", exc)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._owns_browser_process = False

    @property
    def page(self) -> Page | None:
        return self._page

    async def get_page_info(self) -> PageInfo:
        if not self._page:
            raise RuntimeError("Not connected to browser")
        return await build_page_info(self._page)

    async def wait_for_navigation(self, timeout: float = 30.0) -> None:
        if self._page:
            await self._page.wait_for_load_state(
                "networkidle",
                timeout=timeout * 1000,
            )

    async def get_active_page(self) -> Page | None:
        if not self._context:
            return None
        page = await select_active_page(self._context.pages)
        if page is not None:
            self._page = page
        return page

<<<<<<< HEAD
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

    async def neutralize_page(self, url: Optional[str] = None) -> bool:
        """Navigate a staged, submit-ready tab away from the form.

        Call this whenever a fill run is declined or dry-run — a filled ATS
        form left sitting live in the browser is one stray click (or the
        page's own autosave) away from being submitted despite the decline
        (see the 2026-06-03 Tread incident: JobPilot's own confirm prompt
        was declined, but the tab still ended up submitted afterward).

        Matches by exact URL when given, since `get_active_page()`'s
        LinkedIn/Easy-Apply-biased heuristics would often pick the wrong tab
        here. Falls back to whatever page this bridge last touched. Never
        raises — this is best-effort cleanup, not a load-bearing guarantee.
        """
        target = None
        if url and self._context:
            for page in self._context.pages:
                if page.url == url:
                    target = page
                    break
        if target is None:
            target = self._page
        if target is None:
            return False
        try:
            await target.goto("about:blank", timeout=5000)
            log.info("Neutralized staged tab (was: %s)", url or target.url)
            return True
        except Exception as e:
            log.warning("Could not neutralize staged tab: %s", e)
            return False

    async def inject_script(self, script: str) -> any:
        """Inject and execute JavaScript in the page"""
=======
    async def inject_script(self, script: str) -> Any:
>>>>>>> origin/codex/jobpilot-cdp-runtime
        if not self._page:
            raise RuntimeError("Not connected to browser")
        return await self._page.evaluate(script)

    async def query_all(self, selector: str) -> list[Any]:
        if not self._page:
            return []
        return await self._page.query_selector_all(selector)


async def connect_to_chrome(debug_port: int = 9222) -> CDPBridge | None:
    """Connect to Chrome and return the bridge, or None on failure."""
    bridge = CDPBridge(debug_port)
    return bridge if await bridge.connect() else None
