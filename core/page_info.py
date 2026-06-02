"""Page state inspection for the CDP bridge.

URL classification (LinkedIn / job application) + Easy Apply step detection.
Split out of `cdp_bridge.py` to keep it under the HC-1 200-line limit.
"""

from dataclasses import dataclass
from typing import Optional

from playwright.async_api import Page


@dataclass
class PageInfo:
    """Information about the current page state"""
    url: str
    title: str
    is_linkedin: bool
    is_job_application: bool
    application_step: Optional[int] = None


async def build_page_info(page: Page) -> PageInfo:
    """Inspect a Playwright page and return its classified state."""
    url = page.url
    title = await page.title()

    is_linkedin = "linkedin.com" in url
    is_job_application = is_linkedin and (
        "/jobs/view/" in url
        or "jobs/collections" in url
        or "/jobs/search/" in url
    )

    application_step = None
    if is_job_application:
        step_indicator = await page.query_selector(
            '[data-test-modal-id="easy-apply-modal"]'
        )
        if step_indicator:
            progress = await page.query_selector(
                '.jobs-easy-apply-content progress'
            )
            if progress:
                value = await progress.get_attribute("value")
                application_step = int(value) if value else 1

    return PageInfo(
        url=url,
        title=title,
        is_linkedin=is_linkedin,
        is_job_application=is_job_application,
        application_step=application_step,
    )
