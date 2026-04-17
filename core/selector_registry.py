"""
Selector Registry — Self-healing CSS selectors with ARIA-first fallback chains.

LinkedIn frequently changes their markup. Instead of hardcoding selectors
throughout the codebase, all selectors live here with prioritized fallback
chains. When a primary selector fails and a fallback succeeds, we log it
so we know LinkedIn changed something.
"""

from typing import Optional
from playwright.async_api import Page, ElementHandle
from jobpilot.core.logger import get_logger

log = get_logger(__name__)


class SelectorChain:
    """Ordered list of CSS selectors — first match wins."""

    def __init__(self, name: str, selectors: list[str]):
        self.name = name
        self.selectors = selectors
        self._hit_index: dict[str, int] = {}  # track which selector works

    async def query(self, page: Page) -> Optional[ElementHandle]:
        """Return the first element matched by any selector in the chain."""
        for i, sel in enumerate(self.selectors):
            try:
                el = await page.query_selector(sel)
                if el:
                    if i > 0:
                        log.warning(
                            f"[selector-heal] '{self.name}' primary failed, "
                            f"fallback #{i} succeeded: {sel}"
                        )
                    return el
            except Exception:
                continue
        log.warning(f"[selector-heal] '{self.name}' — all {len(self.selectors)} selectors failed")
        return None

    async def query_all(self, page: Page) -> list[ElementHandle]:
        """Return all elements from the first selector that matches anything."""
        for i, sel in enumerate(self.selectors):
            try:
                els = await page.query_selector_all(sel)
                if els:
                    if i > 0:
                        log.warning(
                            f"[selector-heal] '{self.name}' primary failed, "
                            f"fallback #{i} succeeded: {sel}"
                        )
                    return els
            except Exception:
                continue
        return []


# ---------------------------------------------------------------------------
# Selector definitions — ARIA-first, then data-attributes, then classes
# ---------------------------------------------------------------------------

# Easy Apply modal container
EASY_APPLY_MODAL = SelectorChain("easy_apply_modal", [
    '[role="dialog"][aria-label*="Easy Apply"]',
    '[data-test-modal-id="easy-apply-modal"]',
    '.jobs-easy-apply-modal',
    '.artdeco-modal--layer-default',
])

# Progress bar inside Easy Apply
EASY_APPLY_PROGRESS = SelectorChain("easy_apply_progress", [
    '.jobs-easy-apply-content progress',
    '[role="progressbar"]',
    'progress',
])

# Next / Continue button
NEXT_BUTTON = SelectorChain("next_button", [
    '[data-easy-apply-next-button]',
    'button[aria-label*="Continue"]',
    'button[aria-label*="Next"]',
    'button[aria-label*="Review"]',
    'footer button:has-text("Next")',
    'footer button:has-text("Continue")',
    'footer button:has-text("Review")',
])

# Submit / final button
SUBMIT_BUTTON = SelectorChain("submit_button", [
    'button[aria-label*="Submit application"]',
    'button[aria-label*="Submit"]',
    'footer button:has-text("Submit")',
])

# File upload inputs
FILE_INPUTS = SelectorChain("file_inputs", [
    '.jobs-easy-apply-modal input[type="file"]',
    '[role="dialog"] input[type="file"]',
    'input[type="file"]',
])

# Text inputs inside the modal
TEXT_INPUTS = SelectorChain("text_inputs", [
    '.jobs-easy-apply-modal input[type="text"], '
    '.jobs-easy-apply-modal input[type="email"], '
    '.jobs-easy-apply-modal input[type="tel"], '
    '.jobs-easy-apply-modal input[type="number"]',
    '[role="dialog"] input:not([type="file"]):not([type="hidden"]):not([type="checkbox"]):not([type="radio"])',
])

# Textareas inside the modal
TEXTAREAS = SelectorChain("textareas", [
    '.jobs-easy-apply-modal textarea',
    '[role="dialog"] textarea',
])

# Select dropdowns inside the modal
SELECTS = SelectorChain("selects", [
    '.jobs-easy-apply-modal select',
    '[role="dialog"] select',
])

# Radio groups
RADIO_GROUPS = SelectorChain("radio_groups", [
    '.jobs-easy-apply-modal fieldset',
    '.jobs-easy-apply-modal [role="radiogroup"]',
    '[role="dialog"] fieldset',
])

# Form element container (for finding labels)
FORM_ELEMENT = SelectorChain("form_element", [
    '.fb-form-element',
    '.jobs-easy-apply-form-element',
    '.artdeco-text-input',
])

# Dismiss / close button for the modal
DISMISS_BUTTON = SelectorChain("dismiss_button", [
    '[data-test-modal-close-btn]',
    'button[aria-label="Dismiss"]',
    '.artdeco-modal__dismiss',
])

# Job description container (for JD parser)
JD_CONTAINER = SelectorChain("jd_container", [
    '.jobs-description-content',
    '.jobs-description__content',
    '#job-details',
    '[class*="description"] .jobs-box__html-content',
    '.jobs-unified-top-card + div .jobs-box__html-content',
])

# Job title on the listing page
JOB_TITLE = SelectorChain("job_title", [
    '.jobs-unified-top-card__job-title',
    'h1.t-24',
    '.job-details-jobs-unified-top-card__job-title',
    'h1[class*="job-title"]',
])

# Company name on the listing page
COMPANY_NAME = SelectorChain("company_name", [
    '.jobs-unified-top-card__company-name',
    '.job-details-jobs-unified-top-card__company-name',
    'a[class*="company-name"]',
])
