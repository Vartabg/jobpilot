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
        if await self._try_cdp_connect():
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

    async def inject_script(self, script: str) -> Any:
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
