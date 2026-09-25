"""ATS-tailored resume draft generation for JobPilot.

Creates a role-specific markdown resume draft from the local profile, optional
resume text, job description signals, and AI context when a backend is
available (local Bro server or the Gemini API via GEMINI_API_KEY).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import cast

from jobpilot.core import llm_client
from jobpilot.core.bro_client import is_bro_running, query_rag
from jobpilot.core.config import DATA_DIR
from jobpilot.core.job_scorer import JobFitResult, JobScorer
from jobpilot.core.logger import get_logger
from jobpilot.core.profile_store import ProfileStore, UserProfile, get_profile_store

log = get_logger(__name__)

OUTPUT_DIR = DATA_DIR / "resumes"
RESUME_SOURCE_DIR = OUTPUT_DIR
LATEST_DRAFT_FILENAME = "latest_draft.json"
SETTINGS_FILENAME = "settings.json"

# Resume "lanes" let a user keep more than one base resume (e.g. a hands-on
# field-service version and a software version) and have the tailor pick the
# right one per role. The mapping is NOT hardcoded here — it lives in the
# user's own (gitignored) data/settings.json so the shipped code carries no
# personal filenames:
#
#   "resume_lanes": {"facilities": "my_field_resume", "default": "my_sw_resume"}
#
# Values are file stems resolved against RESUME_SOURCE_DIR. With no mapping
# configured the tailor simply uses the profile's resume_path.
FACILITIES_LANE = "facilities"
DEFAULT_LANE = "default"


def _load_resume_lanes(data_dir: Path | None) -> dict[str, str]:
    """Load the optional ``resume_lanes`` mapping from ``<data_dir>/settings.json``.

    Returns an empty mapping when the file, the key, or the value is missing or
    malformed — the tailor then falls back to the profile's ``resume_path``.
    """
    if data_dir is None:
        return {}
    path = Path(data_dir) / SETTINGS_FILENAME
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    lanes = data.get("resume_lanes") if isinstance(data, dict) else None
    if not isinstance(lanes, dict):
        return {}
    return {str(k): str(v) for k, v in lanes.items() if v}


FACILITIES_STRONG_SIGNALS = (
    "field service",
    "service technician",
    "field technician",
    "field engineer",
    "maintenance technician",
    "building automation",
    "bms",
    "biomedical equipment",
    "medical equipment",
    "ev charging",
    "evse",
    "low voltage",
    "electro-mechanical",
    "electromechanical",
    "security systems",
    "x-ray",
    "trace detection",
    "airport equipment",
    "installation technician",
    "controls technician",
)
FACILITIES_WEAK_SIGNALS = (
    "hardware",
    "mechanical",
    "electrical",
    "facilities",
    "hvac",
    "plc",
    "scada",
    "onsite service",
    "commissioning",
    "installation",
    "repair",
    "troubleshooting",
)


@dataclass
class ResumeDraftResult:
    """Result of generating a tailored resume draft."""

    output_path: Path
    fit_result: JobFitResult
    html_path: Path | None = None
    pdf_path: Path | None = None
    matched_skills: list[str] = field(default_factory=lambda: cast(list[str], []))
    summary_lines: list[str] = field(default_factory=lambda: cast(list[str], []))
    highlight_bullets: list[str] = field(default_factory=lambda: cast(list[str], []))
    keywords: list[str] = field(default_factory=lambda: cast(list[str], []))


class ResumeTailor:
    """Generate ATS-friendly resume drafts for specific roles."""

    def __init__(
        self,
        profile_store: ProfileStore | None = None,
        *,
        output_dir: Path | None = None,
        use_bro: bool = True,
        resume_lanes: dict[str, str] | None = None,
    ) -> None:
        self.profile_store = profile_store or get_profile_store()
        self.output_dir = output_dir or OUTPUT_DIR
        self.use_bro = use_bro
        # Lane→stem mapping. Explicit arg wins (used by tests); otherwise load
        # from the profile store's own data dir so it stays co-located with the
        # user's private config and test runs stay hermetic.
        if resume_lanes is not None:
            self.resume_lanes = resume_lanes
        else:
            self.resume_lanes = _load_resume_lanes(
                getattr(self.profile_store, "data_dir", None)
            )
        self.scorer = JobScorer(profile_store=self.profile_store, use_bro=use_bro)

    def generate_from_text(
        self,
        raw_text: str,
        *,
        title: str = "",
        company: str = "",
        output_path: Path | None = None,
        export_html: bool = True,
        export_pdf: bool = False,
    ) -> ResumeDraftResult:
        """Generate a tailored draft from raw JD text."""
        fit_result = self.scorer.score_text(raw_text, title=title, company=company)
        return self._build_draft(
            fit_result,
            output_path=output_path,
            export_html=export_html,
            export_pdf=export_pdf,
        )

    def generate_from_fit_result(
        self,
        fit_result: JobFitResult,
        *,
        output_path: Path | None = None,
        export_html: bool = True,
        export_pdf: bool = False,
    ) -> ResumeDraftResult:
        """Generate a tailored draft from a precomputed fit score result."""
        return self._build_draft(
            fit_result,
            output_path=output_path,
            export_html=export_html,
            export_pdf=export_pdf,
        )

    def _build_draft(
        self,
        fit_result: JobFitResult,
        *,
        output_path: Path | None = None,
        export_html: bool = True,
        export_pdf: bool = False,
    ) -> ResumeDraftResult:
        profile = self.profile_store.load()
        resume_path = self._select_resume_source(profile, fit_result)
        resume_text = self._load_resume_text(resume_path)
        summary_lines = self._build_summary_lines(profile, fit_result, resume_text)
        highlight_bullets = self._build_highlights(profile, fit_result, resume_text)
        keywords = self._build_keywords(fit_result)

        rendered = self._render_markdown(
            profile,
            fit_result,
            summary_lines=summary_lines,
            highlight_bullets=highlight_bullets,
            keywords=keywords,
        )

        target_path = output_path or self._default_output_path(fit_result)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(rendered)

        html_path: Path | None = None
        pdf_path: Path | None = None
        if export_html or export_pdf:
            html_content = self._render_html(
                profile,
                fit_result,
                summary_lines=summary_lines,
                highlight_bullets=highlight_bullets,
                keywords=keywords,
            )
            html_path = target_path.with_suffix(".html")
            html_path.write_text(html_content)

            if export_pdf:
                pdf_path = self._export_pdf(
                    html_content, target_path.with_suffix(".pdf")
                )

        result = ResumeDraftResult(
            output_path=target_path,
            fit_result=fit_result,
            html_path=html_path,
            pdf_path=pdf_path,
            matched_skills=fit_result.matched_skills,
            summary_lines=summary_lines,
            highlight_bullets=highlight_bullets,
            keywords=keywords,
        )
        self._store_latest_draft_manifest(result)
        return result

    def _build_summary_lines(
        self,
        profile: UserProfile,
        fit_result: JobFitResult,
        resume_text: str,
    ) -> list[str]:
        parsed = fit_result.parsed_jd
        matched = fit_result.matched_skills[:5]

        ai_lines = self._maybe_ai_summary_lines(profile, fit_result)
        if ai_lines:
            return ai_lines

        role = parsed.title or profile.current_title or "Software Engineer"
        years = profile.years_of_experience
        company = parsed.company or "the company"
        if self._is_facilities_target(fit_result):
            facilities_focus = (
                ", ".join(matched)
                if matched
                else "electrical troubleshooting, field service operations, customer support, and service documentation"
            )
            summary_lines = [
                f"{role} with {years}+ years across electronics, field service, customer-site troubleshooting, and technical operations."
                if years
                else f"{role} with hands-on electronics, field service, and customer-site troubleshooting experience.",
                f"Strong alignment to {company} needs across {facilities_focus}.",
                self._work_auth_line(profile),
            ]
        else:
            summary_lines = [
                f"{role} with {years}+ years building production software and user-facing workflows."
                if years
                else f"{role} with hands-on experience shipping production software.",
                f"Strong alignment to {company} needs across {', '.join(matched)}."
                if matched
                else f"Targeting {company} with a focus on practical delivery and fast ramp-up.",
                self._work_auth_line(profile),
            ]

        resume_snippets = self._extract_resume_snippets(resume_text, limit=1)
        if resume_snippets:
            summary_lines[1] = resume_snippets[0]

        return [line for line in summary_lines if line]

    def _build_highlights(
        self,
        profile: UserProfile,
        fit_result: JobFitResult,
        resume_text: str,
    ) -> list[str]:
        snippets = self._extract_resume_snippets(resume_text, limit=4)
        if len(snippets) >= 3:
            return snippets[:4]

        parsed = fit_result.parsed_jd
        focus_terms = ", ".join(
            fit_result.matched_skills[:4] or self._build_keywords(fit_result)[:4]
        )
        if not focus_terms:
            focus_terms = "delivery, collaboration, and execution"

        current_focus = profile.current_title or "engineering delivery"
        if profile.current_company:
            current_focus = f"{current_focus} at {profile.current_company}"

        highlights = [
            f"Delivered production work in roles aligned with {parsed.title or 'this target role'}, with emphasis on {focus_terms}.",
            f"Current/most recent focus: {current_focus}.",
            f"Prepared to tailor examples toward {parsed.company or 'the employer'} requirements: {', '.join(self._build_keywords(fit_result)[:5]) or 'relevant role keywords'}.",
        ]

        if profile.github_url:
            highlights.append(
                f"Code samples and shipped work available via {profile.github_url}."
            )

        return [line for line in highlights if line]

    def _build_keywords(self, fit_result: JobFitResult) -> list[str]:
        parsed = fit_result.parsed_jd
        keywords = list(
            dict.fromkeys(
                [*fit_result.matched_skills, *parsed.skills, *fit_result.missing_skills]
            )
        )

        if not keywords:
            # Fall back to short requirement *phrases*, not individual words —
            # "Document service reports" reads as a skill; "Document • service •
            # reports" is word-salad on a resume.
            phrases: list[str] = []
            for item in parsed.requirements:
                phrase = re.sub(r"\s+", " ", item).strip(" .;:-•*")
                # Keep concise, skill-like phrases (drop long sentences).
                if 2 <= len(phrase) <= 60 and len(phrase.split()) <= 7:
                    phrases.append(phrase)
            keywords = list(dict.fromkeys(phrases))

        return keywords[:12]

    def _render_markdown(
        self,
        profile: UserProfile,
        fit_result: JobFitResult,
        *,
        summary_lines: list[str],
        highlight_bullets: list[str],
        keywords: list[str],
    ) -> str:
        parsed = fit_result.parsed_jd
        full_name = (
            f"{profile.first_name} {profile.last_name}"
        ).strip() or "Candidate Name"
        header_bits = [
            bit
            for bit in [
                profile.email,
                profile.phone,
                profile.linkedin_url,
                profile.github_url,
                profile.portfolio_url,
            ]
            if bit
        ]
        header = " | ".join(header_bits)

        lines = [
            f"# {full_name}",
            profile.current_title or parsed.title or "Professional Resume Draft",
            "",
        ]
        if header:
            lines.extend([header, ""])

        lines.extend(
            [
                "## Target Role",
                f"- **Role:** {parsed.title or 'Not specified'}",
                f"- **Company:** {parsed.company or 'Not specified'}",
                f"- **Fit Snapshot:** {fit_result.score}/100 — {fit_result.recommendation}",
                "",
                "## Professional Summary",
            ]
        )
        lines.extend([f"- {line}" for line in summary_lines])

        lines.extend(
            [
                "",
                "## Core Skills",
                f"{' • '.join(keywords[:10]) if keywords else 'Add role-relevant skills here after review.'}",
            ]
        )

        lines.extend(["", "## Tailored Experience Highlights"])
        lines.extend([f"- {line}" for line in highlight_bullets])

        if fit_result.risks:
            lines.extend(["", "## Gaps to Address in Review"])
            lines.extend([f"- {line}" for line in fit_result.risks[:3]])

        lines.extend(
            [
                "",
                "## ATS Notes",
                "- Keep the wording truthful and review before submitting.",
                "- Mirror the job description naturally; do not keyword-stuff.",
                "- Save as PDF after your final edits if the employer requests PDF upload.",
                "",
            ]
        )

        return "\n".join(lines).strip() + "\n"

    def _render_html(
        self,
        profile: UserProfile,
        fit_result: JobFitResult,
        *,
        summary_lines: list[str],
        highlight_bullets: list[str],
        keywords: list[str],
    ) -> str:
        """Render a styled HTML version of the tailored resume."""
        parsed = fit_result.parsed_jd
        full_name = escape(
            (f"{profile.first_name} {profile.last_name}").strip() or "Candidate Name"
        )
        role_title = escape(
            profile.current_title or parsed.title or "Professional Resume Draft"
        )
        contact_bits = [
            escape(bit)
            for bit in [
                profile.email,
                profile.phone,
                profile.linkedin_url,
                profile.github_url,
                profile.portfolio_url,
            ]
            if bit
        ]
        contact_html = " &nbsp;•&nbsp; ".join(contact_bits)

        summary_html = "".join(f"<li>{escape(line)}</li>" for line in summary_lines)
        highlights_html = "".join(
            f"<li>{escape(line)}</li>" for line in highlight_bullets
        )
        gaps_html = "".join(f"<li>{escape(line)}</li>" for line in fit_result.risks[:3])
        keyword_html = "".join(
            f"<span class='chip'>{escape(word)}</span>" for word in keywords[:10]
        )

        return f"""<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>{full_name} — Tailored Resume</title>
  <style>
    :root {{
      --ink: #162033;
      --muted: #5c6880;
      --accent: #2356d8;
      --surface: #f5f7fb;
      --border: #d8dfec;
    }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; color: var(--ink); background: var(--surface); }}
    .page {{ max-width: 860px; margin: 24px auto; background: white; padding: 36px 42px; box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08); }}
    h1 {{ margin: 0; font-size: 30px; }}
    .subtitle {{ margin-top: 6px; font-size: 18px; color: var(--accent); font-weight: 600; }}
    .contact {{ margin-top: 12px; color: var(--muted); font-size: 13px; line-height: 1.5; }}
    .hero {{ border-bottom: 2px solid var(--border); padding-bottom: 16px; margin-bottom: 20px; }}
    h2 {{ font-size: 16px; text-transform: uppercase; letter-spacing: 0.08em; margin: 22px 0 10px; color: var(--accent); }}
    ul {{ margin: 8px 0 0 18px; padding: 0; }}
    li {{ margin: 6px 0; line-height: 1.45; }}
    .meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin: 10px 0 4px; }}
    .meta-card {{ border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; background: #fbfcff; }}
    .meta-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }}
    .meta-value {{ margin-top: 4px; font-weight: 600; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
    .chip {{ background: #eaf0ff; color: #173a96; padding: 6px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
    .note {{ color: var(--muted); font-size: 12px; margin-top: 8px; }}
    @media print {{
      body {{ background: white; }}
      .page {{ box-shadow: none; margin: 0; max-width: none; padding: 22px 26px; }}
      a {{ color: inherit; text-decoration: none; }}
    }}
  </style>
</head>
<body>
  <main class='page'>
    <section class='hero'>
      <h1>{full_name}</h1>
      <div class='subtitle'>{role_title}</div>
      <div class='contact'>{contact_html}</div>
    </section>

    <section>
      <h2>Target Role</h2>
      <div class='meta'>
        <div class='meta-card'><div class='meta-label'>Role</div><div class='meta-value'>{escape(parsed.title or "Not specified")}</div></div>
        <div class='meta-card'><div class='meta-label'>Company</div><div class='meta-value'>{escape(parsed.company or "Not specified")}</div></div>
        <div class='meta-card'><div class='meta-label'>Fit Snapshot</div><div class='meta-value'>{fit_result.score}/100 — {escape(fit_result.recommendation)}</div></div>
      </div>
    </section>

    <section>
      <h2>Professional Summary</h2>
      <ul>{summary_html}</ul>
    </section>

    <section>
      <h2>Core Skills</h2>
      <div class='chips'>{keyword_html or "<span class='chip'>Review and personalize keywords</span>"}</div>
    </section>

    <section>
      <h2>Tailored Experience Highlights</h2>
      <ul>{highlights_html}</ul>
    </section>

    {f"<section><h2>Gaps to Address in Review</h2><ul>{gaps_html}</ul></section>" if gaps_html else ""}

    <section>
      <h2>ATS Notes</h2>
      <ul>
        <li>Keep the wording truthful and review before submitting.</li>
        <li>Mirror the job description naturally; do not keyword-stuff.</li>
        <li>Export to PDF after your final edits if the employer requests PDF upload.</li>
      </ul>
      <div class='note'>Generated by JobPilot for review before submission.</div>
    </section>
  </main>
</body>
</html>
"""

    @staticmethod
    def _export_pdf(html_content: str, pdf_path: Path) -> Path | None:
        """Render the styled HTML as a PDF via Playwright when available."""
        try:
            from playwright.sync_api import sync_playwright

            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_content(html_content, wait_until="load")
                page.pdf(
                    path=str(pdf_path),
                    format="Letter",
                    print_background=True,
                    margin={
                        "top": "0.45in",
                        "right": "0.45in",
                        "bottom": "0.45in",
                        "left": "0.45in",
                    },
                )
                browser.close()
            return pdf_path
        except Exception as exc:
            log.warning("PDF export skipped: %s", exc)
            return None

    def _maybe_ai_summary_lines(
        self,
        profile: UserProfile,
        fit_result: JobFitResult,
    ) -> list[str]:
        if not self.use_bro or not llm_client.is_available():
            return []

        parsed = fit_result.parsed_jd
        # RAG context only exists on the local Bro backend.
        context = ""
        if is_bro_running():
            context = query_rag(
                f"Summarize the candidate background most relevant to {parsed.title or 'this role'} at {parsed.company or 'the company'}",
                top_k=4,
            )
        prompt = (
            "Write exactly 3 ATS-friendly resume summary bullets. "
            "Each bullet must be concise, truthful, and specific to the job target. "
            "Return plain bullets only.\n\n"
            f"Candidate title: {profile.current_title or 'N/A'}\n"
            f"Experience: {profile.years_of_experience} years\n"
            f"Job target: {parsed.summary()}\n"
            f"Matched skills: {', '.join(fit_result.matched_skills) or 'none'}\n"
        )
        try:
            reply = llm_client.complete(prompt, context=context or None, smart=True)
        except llm_client.LLMUnavailable as exc:
            log.debug("AI summary skipped, using template fallback: %s", exc)
            return []

        bullets: list[str] = []
        for line in reply.splitlines():
            stripped = line.strip().lstrip("-•* ").strip()
            if stripped:
                bullets.append(stripped)
        return bullets[:3]

    @staticmethod
    def _extract_resume_snippets(text: str, *, limit: int = 3) -> list[str]:
        snippets: list[str] = []
        for line in text.splitlines():
            raw = line.strip()
            stripped = raw.lstrip("-•* ").strip()
            if not stripped or stripped.startswith("#"):
                continue
            if raw.startswith("**") and raw.endswith("**") and ":" not in raw:
                continue

            cleaned = ResumeTailor._clean_resume_snippet(stripped)
            if not cleaned or "@" in cleaned or "http" in cleaned.lower():
                continue
            if 35 <= len(cleaned) <= 320:
                snippets.append(cleaned)
            if len(snippets) >= limit:
                break
        return snippets

    @staticmethod
    def _clean_resume_snippet(text: str) -> str:
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        cleaned = re.sub(r"\*\*([^:*]+):\*\*\s*", r"\1: ", cleaned)
        cleaned = cleaned.replace("**", "").replace("__", "")
        return " ".join(cleaned.split()).strip()

    @staticmethod
    def _work_auth_line(profile: UserProfile) -> str:
        if profile.authorized_to_work and not profile.requires_sponsorship:
            return "Authorized to work in the United States without sponsorship."
        if profile.requires_sponsorship:
            return (
                "Requires sponsorship; review job authorization requirements carefully."
            )
        return "Review work authorization wording before submitting."

    def _select_resume_source(
        self, profile: UserProfile, fit_result: JobFitResult
    ) -> str:
        """Choose the strongest local base resume for the target lane.

        Lanes come from the user's ``resume_lanes`` config. With no config the
        profile's ``resume_path`` is used unchanged.
        """
        lanes = self.resume_lanes

        if self._is_facilities_target(fit_result):
            stem = lanes.get(FACILITIES_LANE)
            if stem:
                facilities_resume = self._first_existing_resume_source(stem)
                if facilities_resume:
                    return str(facilities_resume)

        default_stem = lanes.get(DEFAULT_LANE)
        if default_stem and self._is_lane_managed_resume(profile.resume_path, lanes):
            default_resume = self._first_existing_resume_source(default_stem)
            if default_resume:
                return str(default_resume)

        return profile.resume_path

    @staticmethod
    def _is_facilities_target(fit_result: JobFitResult) -> bool:
        parsed = fit_result.parsed_jd
        text = " ".join(
            filter(
                None,
                [
                    parsed.title,
                    parsed.company,
                    parsed.raw_text,
                    parsed.summary(),
                    " ".join(parsed.skills),
                    " ".join(parsed.requirements),
                    " ".join(parsed.nice_to_haves),
                ],
            )
        ).lower()

        if any(signal in text for signal in FACILITIES_STRONG_SIGNALS):
            return True

        weak_matches = sum(1 for signal in FACILITIES_WEAK_SIGNALS if signal in text)
        return weak_matches >= 2

    @staticmethod
    def _first_existing_resume_source(stem: str) -> Path | None:
        for suffix in (".md", ".txt", ".pdf"):
            candidate = RESUME_SOURCE_DIR / f"{stem}{suffix}"
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _is_lane_managed_resume(resume_path: str, lanes: dict[str, str]) -> bool:
        """True when the profile's resume is empty or is itself a lane resume.

        In either case the tailor is free to swap in the default-lane resume;
        a resume the user explicitly points at outside the lanes is left alone.
        """
        if not resume_path:
            return True
        stem = Path(resume_path).expanduser().stem
        return stem in set(lanes.values())

    @staticmethod
    def _load_resume_text(resume_path: str) -> str:
        if not resume_path:
            return ""

        path = Path(resume_path).expanduser()
        if not path.exists() or not path.is_file():
            return ""

        suffix = path.suffix.lower()
        if suffix in {".txt", ".md", ".rst"}:
            try:
                return path.read_text()
            except Exception:
                return ""

        if suffix == ".pdf":
            for extractor in (
                ResumeTailor._extract_pdf_with_pypdf,
                ResumeTailor._extract_pdf_with_pdftotext,
                ResumeTailor._extract_pdf_with_mdls,
            ):
                text = extractor(path)
                if text.strip():
                    return text.strip()

        return ""

    @staticmethod
    def _extract_pdf_with_pypdf(path: Path) -> str:
        try:
            from pypdf import PdfReader

            pages = [page.extract_text() or "" for page in PdfReader(str(path)).pages]
            return "\n".join(page.strip() for page in pages if page.strip())
        except Exception as exc:
            log.debug("pypdf extraction unavailable for %s: %s", path, exc)
            return ""

    @staticmethod
    def _extract_pdf_with_pdftotext(path: Path) -> str:
        tool = shutil.which("pdftotext")
        if not tool:
            return ""

        try:
            result = subprocess.run(
                [tool, str(path), "-"],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            if result.returncode != 0:
                log.debug("pdftotext failed for %s: %s", path, result.stderr.strip())
                return ""
            return result.stdout.replace("\f", "\n").strip()
        except Exception as exc:
            log.debug("pdftotext extraction unavailable for %s: %s", path, exc)
            return ""

    @staticmethod
    def _extract_pdf_with_mdls(path: Path) -> str:
        tool = shutil.which("mdls")
        if not tool:
            return ""

        try:
            result = subprocess.run(
                [tool, "-raw", "-name", "kMDItemTextContent", str(path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if result.returncode != 0:
                return ""

            text = result.stdout.strip()
            if text in {"", "(null)"}:
                return ""
            return text.strip('"')
        except Exception as exc:
            log.debug("mdls extraction unavailable for %s: %s", path, exc)
            return ""

    def _store_latest_draft_manifest(self, result: ResumeDraftResult) -> None:
        """Persist the latest tailored resume metadata for the final review gate."""
        manifest_path = self.output_dir / LATEST_DRAFT_FILENAME
        parsed = result.fit_result.parsed_jd
        payload = {
            "markdown_path": str(result.output_path.expanduser().resolve()),
            "html_path": str(result.html_path.expanduser().resolve())
            if result.html_path
            else "",
            "pdf_path": str(result.pdf_path.expanduser().resolve())
            if result.pdf_path
            else "",
            "title": parsed.title or "",
            "company": parsed.company or "",
            "fit_score": result.fit_result.score,
            "recommendation": result.fit_result.recommendation,
            "matched_skills": result.matched_skills[:6],
        }

        try:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(payload, indent=2))
        except Exception as exc:
            log.debug("Could not save latest tailored resume manifest: %s", exc)

    @staticmethod
    def load_latest_draft_summary(
        output_dir: Path | None = None,
    ) -> dict[str, object] | None:
        """Load the most recent tailored resume metadata if available."""
        manifest_path = (output_dir or OUTPUT_DIR) / LATEST_DRAFT_FILENAME
        if not manifest_path.exists():
            return None

        try:
            data = json.loads(manifest_path.read_text())
        except Exception as exc:
            log.debug("Could not read tailored resume manifest: %s", exc)
            return None

        markdown_path = str(data.get("markdown_path", "") or "")
        if not markdown_path:
            return None

        candidate = Path(markdown_path).expanduser()
        if not candidate.is_absolute():
            candidate = (manifest_path.parent / candidate).resolve()
            data["markdown_path"] = str(candidate)

        for key in ("html_path", "pdf_path"):
            raw = str(data.get(key, "") or "")
            if raw:
                path_value = Path(raw).expanduser()
                if not path_value.is_absolute():
                    data[key] = str((manifest_path.parent / path_value).resolve())

        if candidate.exists():
            return data
        return None

    def _default_output_path(self, fit_result: JobFitResult) -> Path:
        parsed = fit_result.parsed_jd
        stamp = hashlib.sha256(
            (parsed.raw_text or parsed.summary()).encode()
        ).hexdigest()[:8]
        company = self._slugify(parsed.company or "company")
        title = self._slugify(parsed.title or "role")
        return self.output_dir / f"resume_{company}_{title}_{stamp}.md"

    @staticmethod
    def _slugify(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug or "item"
