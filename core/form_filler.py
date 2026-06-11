"""
Form Filler — deterministic Playwright-driven form filler for ATS platforms.

No LLM, no browser-use. Just direct DOM manipulation via CDP.
Handles Lever, Greenhouse, Ashby — text inputs, native + custom selects,
autocomplete fields, radio groups, file uploads, required acknowledgments.
Stops before submit.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Page, ElementHandle

from jobpilot.core.logger import get_logger
from jobpilot.core.profile_store import UserProfile
from jobpilot.core.resume_tailor import ResumeTailor

log = get_logger(__name__)

DEFAULT_REFERRAL_SOURCE = "LinkedIn"

# Default answer for EEOC / demographic self-identification questions when the
# user hasn't provided one in their profile: decline to self-identify. This is
# a PIPE-SEPARATED synonym list — the option matchers try each variant in turn
# because every ATS words the "decline" option differently.
DECLINE_TO_SELF_IDENTIFY = (
    "Prefer not to say||I don't wish to answer||I do not wish to answer||"
    "Decline to answer||Decline to self-identify||Decline to self identify||"
    "Don't wish to identify||Rather not say||Choose not to identify"
)

# Where to look in profile.custom_answers when profile.demographics is absent
# (older profiles stored demographic answers as question-keyed custom answers).
_DEMOGRAPHIC_CUSTOM_ANSWER_HINTS: dict[str, tuple[str, ...]] = {
    "gender": ("gender",),
    "sexual_orientation": ("sexual orientation",),
    "hispanic": ("hispanic", "latino"),
    "race": ("race", "ethnicity"),
    "veteran": ("veteran",),
    "disability": ("disability",),
}

DISALLOWED_FORM_LOCATION_PATTERNS = (
    "authorized to work in the uk",
    "authorised to work in the uk",
    "authorized to work in united kingdom",
    "authorised to work in united kingdom",
    "eligible to work in the uk",
    "eligible to work in united kingdom",
    "work in the uk",
    "work in united kingdom",
    "based in the uk",
    "based in united kingdom",
    "commuting distance of london",
    "london office",
    "based in germany",
    "work in germany",
    "professional fluency in german",
    "commuting distance of munich",
    "munich office",
)


@dataclass
class FillResult:
    success: bool
    filled_fields: list[str] = field(default_factory=list)
    skipped_fields: list[str] = field(default_factory=list)
    error: Optional[str] = None
    stopped_before_submit: bool = True
    final_url: Optional[str] = None
    final_title: Optional[str] = None


# ---------------------------------------------------------------------------
# Label → profile-value mapping
# ---------------------------------------------------------------------------

def _field_to_value(label: str, profile: UserProfile) -> Optional[str]:
    l = label.lower()

    if any(k in l for k in ["first name", "firstname", "given name", "first_name"]):
        return profile.first_name
    if any(k in l for k in ["last name", "lastname", "family name", "surname", "last_name"]):
        return profile.last_name
    if "preferred name" in l or "nickname" in l or "goes by" in l:
        return profile.first_name
    if "full name" in l or l.strip() in ("name", "your name", "full_name"):
        return f"{profile.first_name} {profile.last_name}".strip()

    if "email" in l:
        return profile.email
    if any(k in l for k in ["phone", "mobile", "cell", "telephone"]):
        return profile.phone

    if "location (city)" in l or l in ("city", "town"):
        return profile.city
    if l == "state" or "region" in l:
        return profile.state
    if l.strip() == "zip" or "postal" in l or "zip code" in l:
        return profile.zip_code
    if "country" in l:
        return profile.country
    if "location" in l or "based" in l or "address" in l:
        return f"{profile.city}, {profile.state}"

    if "linkedin" in l:
        return profile.linkedin_url
    if "github" in l:
        return profile.github_url
    if any(k in l for k in ["portfolio", "website", "personal site", "blog"]):
        return profile.portfolio_url

    if any(k in l for k in ["current title", "current role", "current position", "job title", "headline"]):
        return profile.current_title
    if any(k in l for k in ["current company", "current employer", "employer", "organization"]):
        return profile.current_company or "Independent"
    if "years of experience" in l or "years experience" in l:
        return str(profile.years_of_experience)
    if "salary" in l or "compensation" in l or "desired pay" in l:
        return profile.desired_salary or "Negotiable"

    # "How did you hear about this job?" — common required field
    if "how did you hear" in l or "how you heard" in l or "hear about" in l or "referral source" in l:
        answers = profile.custom_answers or {}
        for q, a in answers.items():
            if "how did you hear" in q.lower() or "hear about" in q.lower():
                return a
        return DEFAULT_REFERRAL_SOURCE

    return None


def _demographic_value(profile: UserProfile, key: str) -> str:
    """Resolve a demographic (EEOC self-identification) answer.

    Demographic answers are NEVER hardcoded — they belong to the user.
    Sources, in priority order:
      1. ``profile.demographics[key]`` — the dedicated demographics block
         (keys: veteran, gender, race, hispanic, disability,
         sexual_orientation).
      2. ``profile.custom_answers`` — question-keyed answers from older
         profiles (e.g. "Veteran Status": "...").
      3. ``DECLINE_TO_SELF_IDENTIFY`` — when unset we decline to identify
         rather than assert anything on the user's behalf.

    Values may be "||"-separated synonym lists; the option matchers try
    each variant in turn.
    """
    demographics = getattr(profile, "demographics", None)
    if isinstance(demographics, dict):
        value = (demographics.get(key) or "").strip()
        if value:
            return value
    answers = getattr(profile, "custom_answers", None) or {}
    hints = _DEMOGRAPHIC_CUSTOM_ANSWER_HINTS.get(key, ())
    for question, answer in answers.items():
        ql = question.lower()
        if answer and any(h in ql for h in hints):
            return answer
    return DECLINE_TO_SELF_IDENTIFY


def _yesno_for_question(label: str, profile: UserProfile) -> Optional[str]:
    l = label.lower()
    if any(p in l for p in [
        "authorized to work", "legally authorized", "authorization to work",
        "eligible to work", "work in the us", "work in the united states",
        "us citizen", "u.s. citizen", "citizenship",
    ]):
        return "Yes" if profile.authorized_to_work else "No"
    if any(p in l for p in [
        "sponsorship", "visa sponsorship", "require sponsorship",
        "need sponsorship", "will you require employment",
    ]):
        return "No" if not profile.requires_sponsorship else "Yes"
    if any(p in l for p in [
        "willing to work", "open to work", "open to hybrid", "on-site", "in-office",
        "hub location", "able to commute", "willing to commute",
        "willing to relocate", "open to relocation", "able to relocate",
        "will you relocate", "comfortable relocating",
        "days per week in", "days a week in", "days in the office",
        "comfortable with hybrid", "hybrid work schedule", "hybrid schedule",
        "office presence", "onsite requirement",
    ]):
        return "Yes"
    # Don't preemptively request relocation ASSISTANCE — skip those, user reviews
    if any(p in l for p in [
        "relocation assistance", "require relocation", "need relocation",
        "relocation package",
    ]):
        return None  # user decides — better to leave blank than say Yes too early

    # EEOC / self-identification questions — answers come from the user's
    # profile via _demographic_value (default: decline to self-identify).
    # Returns a PIPE-SEPARATED synonym list so the select/dropdown matcher
    # can find ANY of these substrings in the option text. The answer_selects
    # code splits on "||" and tries each.
    if "gender identity" in l or ("gender" in l and "violence" not in l):
        return _demographic_value(profile, "gender")
    if "sexual orientation" in l:
        return _demographic_value(profile, "sexual_orientation")
    if "hispanic" in l or "latino" in l:
        return _demographic_value(profile, "hispanic")
    if "race" in l or "ethnicity" in l:
        return _demographic_value(profile, "race")
    if "veteran" in l:
        return _demographic_value(profile, "veteran")
    if "disability" in l or "disabled" in l:
        return _demographic_value(profile, "disability")

    # Okta-style conflict-of-interest / prior-employment dropdowns — default No
    if any(p in l for p in [
        "family member", "close personal relationship", "relative who works",
        "outside business activity", "outside business",
        "worked for", "past employee of", "previous employment with",
        "currently live within", "live within 50 miles", "live within",
        "conflict of interest", "improperly bias",
    ]):
        return "No"

    answers = profile.custom_answers or {}

    # Direct custom answer match (substring in either direction)
    for q, a in answers.items():
        ql = q.lower()
        if ql in l or l in ql:
            return a
    return None


def _normalize_option_text(text: str) -> str:
    return " ".join((text or "").lower().split())


def _radio_option_matches(candidate: str, radio_label: str, radio_value: str) -> bool:
    """Match a radio option strictly — exact text or whole-word match only.

    Plain substring matching is too loose here: the answer "No" must not
    match "None of the above" or "Not sure". Mirrors the stricter select
    matcher: exact normalized equality first, then a word-boundary search
    for answers embedded in verbose labels ("No, I do not require...").
    """
    needle = _normalize_option_text(candidate)
    if not needle:
        return False
    for haystack in (_normalize_option_text(radio_label), _normalize_option_text(radio_value)):
        if not haystack:
            continue
        if needle == haystack:
            return True
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
            return True
    return False


def _detect_disallowed_form_location(text: str) -> Optional[str]:
    normalized = " ".join((text or "").lower().split())
    for pattern in DISALLOWED_FORM_LOCATION_PATTERNS:
        if pattern in normalized:
            return pattern
    return None


# ---------------------------------------------------------------------------
# DOM helpers
# ---------------------------------------------------------------------------

async def _label_text(page: Page, element: ElementHandle) -> str:
    try:
        return await page.evaluate(
            """el => {
                if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
                if (el.getAttribute('aria-labelledby')) {
                    const ref = document.getElementById(el.getAttribute('aria-labelledby'));
                    if (ref) return ref.innerText.trim();
                }
                const id = el.id;
                if (id) {
                    const lbl = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                    if (lbl) return lbl.innerText.trim();
                }
                let p = el.parentElement;
                for (let i = 0; i < 5 && p; i++) {
                    if (p.tagName === 'LABEL') return p.innerText.trim();
                    const lbl = p.querySelector('label, legend, .application-label');
                    if (lbl && !lbl.contains(el)) return lbl.innerText.trim();
                    if (p.tagName === 'FIELDSET') {
                        const leg = p.querySelector('legend');
                        if (leg) return leg.innerText.trim();
                    }
                    p = p.parentElement;
                }
                const prev = el.previousElementSibling;
                if (prev && prev.innerText) return prev.innerText.trim();
                return el.getAttribute('placeholder') || el.name || el.id || '';
            }""",
            element,
        ) or ""
    except Exception:
        return ""


async def _click_apply_button(page: Page) -> bool:
    selectors = [
        'a.postings-btn[href*="apply"]',
        'a.posting-btn-submit',
        'a[href*="/apply"]',
        'button#apply_button',
        'a.apply',
        'button:has-text("Apply for this job")',
        'button:has-text("Apply for this Job")',
        'button:has-text("Apply now")',
        'a:has-text("Apply for this job")',
        'a:has-text("Apply for this Job")',
        'a:has-text("Apply now")',
    ]
    for sel in selectors:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
                log.info("Clicked apply button: %s", sel)
                return True
        except Exception:
            continue
    try:
        clicked = await page.evaluate(
            """() => {
                const els = Array.from(document.querySelectorAll('button, a'));
                const target = els.find(el => /apply for this job|apply now/i.test(el.innerText || el.textContent || ''));
                if (!target) return false;
                target.click();
                return true;
            }"""
        )
        if clicked:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
            log.info("Clicked apply button with text fallback")
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Field handlers
# ---------------------------------------------------------------------------

async def _fill_text_inputs(page: Page, profile: UserProfile) -> tuple[list[str], list[str]]:
    filled, skipped = [], []
    selector = (
        'input[type="text"]:not([disabled]):not([readonly]), '
        'input[type="email"]:not([disabled]):not([readonly]), '
        'input[type="tel"]:not([disabled]):not([readonly]), '
        'input[type="url"]:not([disabled]):not([readonly]), '
        'input[type="number"]:not([disabled]):not([readonly]), '
        'input:not([type]):not([disabled]):not([readonly]), '
        'textarea:not([disabled]):not([readonly])'
    )
    inputs = await page.query_selector_all(selector)
    for el in inputs:
        try:
            if not await el.is_visible():
                continue
            current = (await el.input_value()) or ""
            if current.strip():
                continue

            name = (await el.get_attribute("name")) or ""
            placeholder = (await el.get_attribute("placeholder")) or ""
            label = await _label_text(page, el)
            combined = f"{label} {name} {placeholder}".strip()

            # Handle autocomplete fields (Location City etc.) differently
            is_autocomplete = (
                "location" in label.lower() and "city" in label.lower()
            ) or (await el.get_attribute("role") == "combobox") or (
                await el.get_attribute("aria-autocomplete") in ("list", "both")
            )

            value = _field_to_value(combined, profile)
            if not value:
                skipped.append(combined[:60])
                continue

            if is_autocomplete:
                await el.click()
                await el.type(value, delay=50)
                await asyncio.sleep(1.3)
                try:
                    await el.press("ArrowDown")
                    await asyncio.sleep(0.3)
                    await el.press("Enter")
                except Exception:
                    pass
                filled.append(f"{combined[:50]} → {value[:40]} (autocomplete)")
            else:
                await el.fill(value)
                # Fire change/input events for React-controlled forms
                try:
                    await page.evaluate(
                        """el => {
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }""",
                        el,
                    )
                except Exception:
                    pass
                filled.append(f"{combined[:50]} → {value[:40]}")
            await asyncio.sleep(0.15)
        except Exception as e:
            log.debug("Could not fill input: %s", e)
            continue
    return filled, skipped


async def _verify_upload(page: Page, filename: str) -> bool:
    """Confirm the resume actually attached — check DOM for the filename."""
    try:
        found = await page.evaluate(
            """fname => {
                const body = document.body ? document.body.innerText : '';
                if (body.includes(fname)) return true;
                // Common success patterns
                const patterns = [
                    '[class*="file-name" i]', '[class*="filename" i]',
                    '[class*="uploaded" i]', '[class*="attachment" i]',
                    '[class*="resume-name" i]',
                ];
                for (const sel of patterns) {
                    const nodes = document.querySelectorAll(sel);
                    for (const n of nodes) {
                        if ((n.innerText || '').includes(fname)) return true;
                    }
                }
                // Any file input reporting a non-empty files list
                const inputs = document.querySelectorAll('input[type="file"]');
                for (const inp of inputs) {
                    if (inp.files && inp.files.length > 0) {
                        const f = inp.files[0];
                        if (f.name === fname) return true;
                    }
                }
                return false;
            }""",
            filename,
        )
        return bool(found)
    except Exception:
        return False


def _preferred_resume_upload(profile: UserProfile) -> tuple[Optional[Path], str]:
    """Prefer the latest tailored resume PDF, then fall back to profile resume."""
    latest_draft = ResumeTailor.load_latest_draft_summary()
    if latest_draft:
        pdf_path = str(latest_draft.get("pdf_path", "") or "")
        if pdf_path:
            tailored_pdf = Path(pdf_path).expanduser()
            if tailored_pdf.exists() and tailored_pdf.is_file():
                return tailored_pdf, "tailored"

    resume_path = getattr(profile, "resume_path", "") or ""
    if resume_path:
        candidate = Path(resume_path).expanduser()
        if candidate.exists() and candidate.is_file():
            return candidate, "profile"

    return None, "missing"


async def _upload_resume(page: Page, profile: UserProfile) -> Optional[str]:
    resume, source = _preferred_resume_upload(profile)
    if resume is None:
        return f"Resume not found at {profile.resume_path}"

    async def _try_upload(inputs) -> bool:
        for fi in inputs:
            try:
                await fi.set_input_files(str(resume))
                suffix = " (tailored)" if source == "tailored" else ""
                log.info("Uploaded resume%s: %s", suffix, resume.name)
                return True
            except Exception as e:
                log.debug("File input upload attempt failed: %s", e)
                continue
        return False

    try:
        # Attempt 1: existing file inputs (visible or hidden)
        file_inputs = await page.query_selector_all('input[type="file"]')
        if await _try_upload(file_inputs):
            await asyncio.sleep(1.2)  # let the UI register the file
            if await _verify_upload(page, resume.name):
                return None
            log.info("Upload set but filename not detected — continuing with Attach fallback")

        # Attempt 2: click an "Attach" / "Upload" button to reveal a file input.
        attach_selectors = [
            'button:has-text("Attach")',
            'button:has-text("Upload")',
            'label:has-text("Attach")',
            'label:has-text("Upload")',
            'a:has-text("Attach")',
            'a:has-text("Upload")',
            '[aria-label*="attach" i]',
            '[aria-label*="upload resume" i]',
            '[class*="attach" i][role="button"]',
        ]
        for sel in attach_selectors:
            try:
                btn = await page.query_selector(sel)
                if not btn or not await btn.is_visible():
                    continue
                await btn.click()
                await asyncio.sleep(0.8)
                file_inputs = await page.query_selector_all('input[type="file"]')
                if await _try_upload(file_inputs):
                    await asyncio.sleep(1.2)
                    if await _verify_upload(page, resume.name):
                        return None
            except Exception as e:
                log.debug("Attach-button click failed (%s): %s", sel, e)
                continue

        # Last attempt: we may have uploaded but verification failed.
        # If any file input has the file set, count it as success with caveat.
        any_attached = await page.evaluate(
            """() => {
                const inputs = document.querySelectorAll('input[type="file"]');
                for (const inp of inputs) {
                    if (inp.files && inp.files.length > 0) return inp.files[0].name;
                }
                return null;
            }"""
        )
        if any_attached:
            return f"Uploaded (unverified in DOM): {any_attached}"
        return "No file input found for resume (tried Attach/Upload buttons)"
    except Exception as e:
        return f"Resume upload error: {e}"


async def _answer_yesno_radios(page: Page, profile: UserProfile) -> list[str]:
    answered = []
    groups = await page.query_selector_all('fieldset, div[role="radiogroup"], ul.application-question')
    for group in groups:
        try:
            question = await page.evaluate(
                """el => {
                    const q = el.querySelector('legend, label, .application-label, [class*="label" i]');
                    return q ? q.innerText.trim() : el.innerText.slice(0, 200);
                }""",
                group,
            )
            if not question:
                continue
            target = _yesno_for_question(question, profile)
            if not target:
                continue
            radios = await group.query_selector_all('input[type="radio"]')
            options = []
            for radio in radios:
                try:
                    radio_label = await _label_text(page, radio)
                    radio_value = (await radio.get_attribute("value")) or ""
                    options.append((radio, radio_label, radio_value))
                except Exception:
                    continue
            # Try each "||" synonym in priority order; exact/word-boundary
            # match only — "No" must not grab "None of the above".
            candidates = [t.strip() for t in target.split("||")] if "||" in target else [target]
            for cand in candidates:
                match = next(
                    (
                        (radio, radio_label)
                        for radio, radio_label, radio_value in options
                        if _radio_option_matches(cand, radio_label, radio_value)
                    ),
                    None,
                )
                if match:
                    radio, radio_label = match
                    await radio.check()
                    answered.append(f"{question[:50]} → {(radio_label or cand)[:40]}")
                    await asyncio.sleep(0.15)
                    break
        except Exception as e:
            log.debug("Radio group handling failed: %s", e)
            continue
    return answered


async def _answer_all_selects(page: Page, profile: UserProfile) -> tuple[list[str], list[str]]:
    """Answer every <select> dropdown — even hidden ones (Select2 hides the native).

    Strategy:
      1. Try to match label → profile value (country, etc.) OR yes/no question.
      2. Set the native select value via JS and dispatch change events
         (works with Select2 / React Select wrappers).
      3. Fall back to Playwright's select_option if element is visible.
    """
    filled, skipped = [], []
    selects = await page.query_selector_all("select:not([disabled])")
    for sel in selects:
        try:
            current = await sel.input_value()
            if current and current.strip() and current.lower() not in ("", "select", "choose"):
                continue

            label = await _label_text(page, sel)
            l_lower = label.lower()

            # Decide target value
            target = _field_to_value(label, profile) or _yesno_for_question(label, profile)
            if not target:
                skipped.append(f"[select] {label[:60]}")
                continue

            # Collect options
            options = await page.evaluate(
                """el => Array.from(el.options).map(o => ({ value: o.value, text: (o.text || '').trim() }))""",
                sel,
            )
            # Find best match — if target contains "||" try each synonym
            def _match(opts, needle):
                n = needle.lower()
                # Exact text/value
                for o in opts:
                    if o["text"].strip().lower() == n or o["value"].strip().lower() == n:
                        return o
                # Substring
                for o in opts:
                    if n in o["text"].lower() or n in o["value"].lower():
                        return o
                # For "United States", also try "United States of America" / "US"
                if n in ("united states", "usa", "us"):
                    for o in opts:
                        t = o["text"].lower()
                        if "united states" in t or t == "us" or t == "usa":
                            return o
                return None

            # If target has synonyms, try each until one matches
            candidates = [t.strip() for t in target.split("||")] if "||" in target else [target]
            match = None
            for cand in candidates:
                match = _match(options, cand)
                if match:
                    break
            if not match:
                skipped.append(f"[select] {label[:50]} — no match for '{target}'")
                continue

            # Set via JS to support hidden Select2/React selects
            try:
                await page.evaluate(
                    """([el, val]) => {
                        el.value = val;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        // jQuery Select2 compatibility
                        if (window.jQuery && jQuery(el).data('select2')) {
                            jQuery(el).val(val).trigger('change');
                        }
                    }""",
                    [sel, match["value"]],
                )
                filled.append(f"{label[:50]} → {match['text'][:40]}")
                await asyncio.sleep(0.15)
            except Exception as e:
                log.debug("JS select failed for %s: %s", label, e)
                skipped.append(f"[select] {label[:50]} (set failed)")
        except Exception as e:
            log.debug("Select handling error: %s", e)
            continue
    return filled, skipped


async def _answer_react_dropdowns(page: Page, profile: UserProfile) -> tuple[list[str], list[str]]:
    """Handle custom React-style dropdowns (role=combobox or div.select__control).

    These are NOT native <select> elements. Pattern:
      1. Click the trigger element to open options
      2. Look for an <li>/<div> matching the target value
      3. Click it
    """
    filled, skipped = [], []

    # Find custom dropdown triggers. Greenhouse's new UI uses a div wrapper
    # with a placeholder "Select..." — the clickable element is a button or
    # div with role="combobox" or class containing "select__control".
    triggers = await page.query_selector_all(
        'div[class*="select__control" i], '
        'div[role="combobox"], '
        'button[role="combobox"], '
        '[class*="select__indicator"] ~ *, '
        'div[class*="Select-control"]'
    )
    seen_ids = set()
    for trigger in triggers:
        try:
            if not await trigger.is_visible():
                continue
            # Dedupe — some selectors overlap
            trigger_id = await trigger.evaluate("el => el.outerHTML.slice(0, 100)")
            if trigger_id in seen_ids:
                continue
            seen_ids.add(trigger_id)

            # Find label by walking up to the containing field
            label = await page.evaluate(
                """el => {
                    let p = el;
                    for (let i = 0; i < 6 && p; i++) {
                        const lbl = p.querySelector ? p.querySelector('label, legend, [class*="label" i]') : null;
                        if (lbl && !lbl.contains(el) && lbl.innerText.trim().length > 0) {
                            return lbl.innerText.trim();
                        }
                        if (p.previousElementSibling) {
                            const sib = p.previousElementSibling;
                            if (sib.tagName === 'LABEL' || /label/i.test(sib.className || '')) {
                                return sib.innerText.trim();
                            }
                        }
                        p = p.parentElement;
                    }
                    return '';
                }""",
                trigger,
            )
            if not label:
                continue

            target = _field_to_value(label, profile) or _yesno_for_question(label, profile)
            if not target:
                skipped.append(f"[react-dropdown] {label[:50]}")
                continue

            # Check if already filled (trigger text includes something other than "Select..." / "Choose...")
            current_text = (await trigger.inner_text()).strip().lower()
            if current_text and current_text not in ("select...", "select", "choose...", "choose", ""):
                if target.lower() in current_text:
                    continue  # already correct
                # else: user had a stale value — leave alone

            # Click to open
            await trigger.click()
            await asyncio.sleep(0.4)

            # Look for an option matching target. Target may contain "||"
            # synonyms — try each until one matches.
            targets_list = [t.strip() for t in target.split("||")] if "||" in target else [target]
            option = await page.evaluate(
                """targets => {
                    const norm = s => (s || '').trim().toLowerCase();
                    const candidates = Array.from(document.querySelectorAll(
                        '[class*="option" i]:not([class*="group" i]), ' +
                        '[role="option"], ' +
                        'li[class*="select" i]'
                    ));
                    for (const raw of targets) {
                        const needle = norm(raw);
                        let found = candidates.find(c => norm(c.innerText) === needle);
                        if (!found) found = candidates.find(c => norm(c.innerText).includes(needle));
                        if (!found) {
                            const firstWord = needle.split(' ')[0];
                            found = candidates.find(c => norm(c.innerText).includes(firstWord));
                        }
                        if (found) {
                            found.scrollIntoView({ block: 'center' });
                            found.click();
                            return found.innerText.trim();
                        }
                    }
                    return null;
                }""",
                targets_list,
            )
            if option:
                filled.append(f"{label[:50]} → {option[:40]}")
                await asyncio.sleep(0.2)
            else:
                # Close the dropdown by pressing Escape
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                skipped.append(f"[react-dropdown] {label[:50]} — no option '{target}'")
        except Exception as e:
            log.debug("React dropdown handling failed: %s", e)
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            continue
    return filled, skipped


async def _check_required_acknowledgments(page: Page) -> list[str]:
    """Check required acknowledgment checkboxes (privacy policy, terms)."""
    checked = []
    checkboxes = await page.query_selector_all('input[type="checkbox"]:not([disabled])')
    for cb in checkboxes:
        try:
            if not await cb.is_visible():
                continue
            if await cb.is_checked():
                continue

            label = await _label_text(page, cb)
            l = label.lower()

            required_attr = (await cb.get_attribute("required")) is not None or (
                await cb.get_attribute("aria-required") == "true"
            )
            ack_keywords = [
                "acknowledge", "i agree", "agree to", "accept", "confirm",
                "privacy policy", "terms", "consent to", "read and understand",
            ]
            looks_like_ack = any(k in l for k in ack_keywords)

            # Skip marketing opt-ins unless also required
            is_marketing = any(m in l for m in [
                "marketing", "newsletter", "promotional", "updates about",
                "sign me up", "notifications",
            ])

            if (required_attr or looks_like_ack) and not is_marketing:
                try:
                    await cb.check()
                except Exception:
                    # Some checkboxes need a label click
                    try:
                        await page.evaluate(
                            """el => {
                                el.checked = true;
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                            }""",
                            cb,
                        )
                    except Exception:
                        continue
                checked.append(f"☑ {label[:60]} [checked for you — please read]")
                await asyncio.sleep(0.15)
        except Exception as e:
            log.debug("Checkbox handling failed: %s", e)
            continue
    return checked


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def fill_application(
    job_url: str,
    job_title: str,
    company: str,
    profile: UserProfile,
    cdp_port: int = 9222,
) -> FillResult:
    cdp_url = f"http://127.0.0.1:{cdp_port}"
    log.info("Filling form for %s @ %s", job_title, company)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(cdp_url)
            if not browser.contexts:
                return FillResult(success=False, error="No Chrome contexts — is Chrome open with debug port?")
            ctx = browser.contexts[0]
            page = await ctx.new_page()

            try:
                await page.goto(job_url, wait_until="commit", timeout=20000)
                await page.bring_to_front()
                for _ in range(30):
                    try:
                        ready = await page.evaluate("document.readyState")
                        if ready in ("interactive", "complete"):
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(1)
                await asyncio.sleep(2)
            except Exception as e:
                log.warning("Navigation commit failed, trying window.location: %s", e)
                try:
                    # Parameterized — never interpolate ATS-sourced URLs into JS
                    await page.evaluate("u => { window.location.href = u; }", job_url)
                    await asyncio.sleep(8)
                except Exception as e2:
                    return FillResult(success=False, error=f"Could not load {job_url}: {e2}")

            # Click Apply button if we're on the job-listing page instead of the apply form
            if "/apply" not in page.url:
                clicked = await _click_apply_button(page)
                if clicked:
                    await asyncio.sleep(2)

            # Some ATS pages (MongoDB, Atlassian, etc.) redirect to company-
            # branded pages where the form is lazy-loaded at the bottom. Scroll
            # through the page to force-render the form, then scroll to it.
            try:
                await page.evaluate(
                    """async () => {
                        const sleep = (ms) => new Promise(r => setTimeout(r, ms));
                        const step = Math.max(400, Math.floor(window.innerHeight * 0.8));
                        let y = 0;
                        while (y < document.body.scrollHeight) {
                            window.scrollTo(0, y);
                            await sleep(120);
                            y += step;
                        }
                        window.scrollTo(0, document.body.scrollHeight);
                        await sleep(400);
                        // Scroll back to the first form input so user sees what we filled
                        const firstInput = document.querySelector(
                            'input[type="text"]:not([disabled]), input[type="email"]:not([disabled]), ' +
                            'input[name*="first" i], input[id*="first" i]'
                        );
                        if (firstInput) firstInput.scrollIntoView({ behavior: 'instant', block: 'center' });
                    }"""
                )
                await asyncio.sleep(1.5)
            except Exception as e:
                log.debug("Scroll-to-form failed: %s", e)

            all_filled: list[str] = []
            all_skipped: list[str] = []

            page_text = await page.locator("body").inner_text(timeout=5000)
            disallowed_location = _detect_disallowed_form_location(page_text)
            if disallowed_location:
                return FillResult(
                    success=False,
                    error=(
                        "Disallowed international location detected on form: "
                        f"{disallowed_location}"
                    ),
                    stopped_before_submit=True,
                    final_url=page.url,
                    final_title=await page.title(),
                )

            # Gather every frame: main page + any embedded iframes (Greenhouse,
            # Lever, Ashby often embed the form in an iframe on the company's
            # own careers page).
            frames_to_fill = [page]
            try:
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    url = frame.url or ""
                    if any(k in url for k in [
                        "greenhouse.io", "lever.co", "ashbyhq.com",
                        "boards.greenhouse", "job-boards.greenhouse",
                        "/embed/",
                    ]):
                        frames_to_fill.append(frame)
                        log.info("Will also fill iframe: %s", url[:80])
            except Exception as e:
                log.debug("Frame enumeration failed: %s", e)

            for frame in frames_to_fill:
                try:
                    text_filled, text_skipped = await _fill_text_inputs(frame, profile)
                    all_filled.extend(text_filled)
                    all_skipped.extend(text_skipped)

                    radio_filled = await _answer_yesno_radios(frame, profile)
                    all_filled.extend(radio_filled)

                    select_filled, select_skipped = await _answer_all_selects(frame, profile)
                    all_filled.extend(select_filled)
                    all_skipped.extend(select_skipped)

                    react_filled, react_skipped = await _answer_react_dropdowns(frame, profile)
                    all_filled.extend(react_filled)
                    all_skipped.extend(react_skipped)

                    ack_checked = await _check_required_acknowledgments(frame)
                    all_filled.extend(ack_checked)
                except Exception as e:
                    log.debug("Frame fill error on %s: %s", getattr(frame, "url", "?"), e)

            # Upload resume (try every frame's file inputs)
            uploaded = False
            for frame in frames_to_fill:
                err = await _upload_resume(frame, profile)
                if not err:
                    uploaded = True
                    break
            if uploaded:
                resume_path, source = _preferred_resume_upload(profile)
                resume_name = resume_path.name if resume_path else Path(profile.resume_path).name
                suffix = " (tailored)" if source == "tailored" else ""
                all_filled.append(f"📎 Resume: {resume_name}{suffix}")
            else:
                all_skipped.append("Resume: no file input found")

            # Detect CAPTCHA / reCAPTCHA / hCaptcha — these block automated
            # submits. Flag it so the phone reviewer knows to solve it on Mac.
            for frame in frames_to_fill:
                try:
                    has_captcha = await frame.evaluate(
                        """() => {
                            const markers = [
                                'iframe[src*="recaptcha"]',
                                'iframe[src*="captcha"]',
                                'iframe[src*="hcaptcha"]',
                                '.g-recaptcha',
                                '[data-sitekey]',
                                '#g-recaptcha',
                                '[class*="recaptcha" i]',
                            ];
                            return markers.some(s => document.querySelector(s));
                        }"""
                    )
                    if has_captcha:
                        all_skipped.append(
                            "⚠ CAPTCHA detected — solve 'I'm not a robot' in Chrome on your Mac before tapping Submit"
                        )
                        break
                except Exception:
                    continue

            await page.bring_to_front()

            # Capture final URL and title for later tab-focus matching
            try:
                final_url = page.url
                final_title = await page.title()
            except Exception:
                final_url = None
                final_title = None

            return FillResult(
                success=True,
                filled_fields=all_filled,
                skipped_fields=all_skipped,
                stopped_before_submit=True,
                final_url=final_url,
                final_title=final_title,
            )

    except Exception as e:
        log.error("Form filler failed: %s", e)
        return FillResult(success=False, error=str(e))


async def submit_application(cdp_port: int = 9222) -> tuple[bool, str]:
    """Submit is disabled by policy.

    JobPilot may fill and stage applications, but the user clicks the final
    submit button themselves. Keep this function as a hard fence so future
    integrations cannot accidentally resurrect an auto-submit path by
    importing it.
    """
    return False, "Auto-submit disabled: you must click the final submit button yourself."
