"""Select the safest useful page from a Playwright browser context."""

from __future__ import annotations

from collections.abc import Sequence

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

EASY_APPLY_SELECTOR = (
    '[role="dialog"][aria-label*="Easy Apply"], '
    '[data-test-modal-id="easy-apply-modal"], '
    ".jobs-easy-apply-modal"
)


def is_injectable(url: str) -> bool:
    return url.startswith(("http://", "https://"))


async def select_active_page(pages: Sequence[Page]) -> Page | None:
    """Prefer an Easy Apply tab, then LinkedIn, then any injectable page."""
    for page in pages:
        if "linkedin.com" not in page.url:
            continue
        try:
            if await page.query_selector(EASY_APPLY_SELECTOR):
                return page
        except PlaywrightError:
            continue
    for page in pages:
        if "linkedin.com" in page.url:
            return page
    return next((page for page in pages if is_injectable(page.url)), None)
