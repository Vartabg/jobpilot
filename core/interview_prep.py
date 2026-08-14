"""Interview prep brief generation for JobPilot.

Turns a scored job description plus the local candidate profile into a
one-page HTML interview brief: a 60-second opener, likely interview
questions, behavioral (STAR) themes to prepare, gap/location notes to get
ahead of, questions to ask the interviewer, and honest reminders.

Design constraints (deliberate, and safe for anyone who uses the tool):
* The STAR section suggests *themes and where to anchor them* — it never
  invents a story, an accomplishment, or a years-of-experience count.
* The AI backend (local Bro or Gemini via ``llm_client``) is optional. When
  it is unavailable the brief is fully produced from templates. AI is used
  only to sharpen the opener and the likely-question list — it is instructed
  never to fabricate facts.

This mirrors ``resume_tailor.ResumeTailor`` in shape and reuse so the two
stages stay consistent.
"""

from __future__ import annotations

import hashlib
import json
import re
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
from jobpilot.core.resume_tailor import ResumeTailor

log = get_logger(__name__)

OUTPUT_DIR = DATA_DIR / "reports"
LATEST_PREP_FILENAME = "latest_prep.json"

# Behavioral themes almost every interview probes. Kept fixed and template-only
# so the tool never fabricates a candidate's story — it only names the theme
# and reminds the user to anchor it to a real, specific example.
STAR_THEMES: tuple[tuple[str, str], ...] = (
    ("A hard problem you solved", "Pick one real example. Walk the diagnosis step by step; end on the result."),
    ("A difficult customer or stakeholder", "Show how you communicated honestly under pressure — even with bad news."),
    ("Working under a deadline", "One time you owned an outcome with little support. End on what you delivered."),
    ("Learning something new fast", "How you ramp on an unfamiliar system. Keep it to ~30 seconds."),
)


@dataclass
class InterviewPrepResult:
    """Result of generating an interview prep brief."""

    output_path: Path
    fit_result: JobFitResult
    opener: str = ""
    likely_questions: list[str] = field(default_factory=lambda: cast(list[str], []))
    star_themes: list[tuple[str, str]] = field(default_factory=lambda: cast(list[tuple[str, str]], []))
    gap_notes: list[str] = field(default_factory=lambda: cast(list[str], []))
    questions_to_ask: list[str] = field(default_factory=lambda: cast(list[str], []))
    reminders: list[str] = field(default_factory=lambda: cast(list[str], []))


class InterviewPrepGenerator:
    """Generate a one-page interview prep brief for a target role."""

    def __init__(
        self,
        profile_store: ProfileStore | None = None,
        *,
        output_dir: Path | None = None,
        use_bro: bool = True,
    ) -> None:
        self.profile_store = profile_store or get_profile_store()
        self.output_dir = output_dir or OUTPUT_DIR
        self.use_bro = use_bro
        self.scorer = JobScorer(profile_store=self.profile_store, use_bro=use_bro)

    # -- entry points (mirror ResumeTailor) --------------------------------

    def generate_from_text(
        self,
        raw_text: str,
        *,
        title: str = "",
        company: str = "",
        output_path: Path | None = None,
        export_pdf: bool = False,
    ) -> InterviewPrepResult:
        """Generate a brief from raw JD text."""
        fit_result = self.scorer.score_text(raw_text, title=title, company=company)
        return self._build_brief(fit_result, output_path=output_path, export_pdf=export_pdf)

    def generate_from_fit_result(
        self,
        fit_result: JobFitResult,
        *,
        output_path: Path | None = None,
        export_pdf: bool = False,
    ) -> InterviewPrepResult:
        """Generate a brief from a precomputed fit score result."""
        return self._build_brief(fit_result, output_path=output_path, export_pdf=export_pdf)

    # -- build -------------------------------------------------------------

    def _build_brief(
        self,
        fit_result: JobFitResult,
        *,
        output_path: Path | None = None,
        export_pdf: bool = False,
    ) -> InterviewPrepResult:
        profile = self.profile_store.load()

        opener = self._build_opener(profile, fit_result)
        likely_questions = self._build_likely_questions(fit_result)
        gap_notes = self._build_gap_notes(profile, fit_result)
        questions_to_ask = self._build_questions_to_ask(fit_result)
        reminders = self._build_reminders(profile, fit_result)
        star_themes = list(STAR_THEMES)

        target_path = output_path or self._default_output_path(fit_result)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        html_content = self._render_html(
            profile,
            fit_result,
            opener=opener,
            likely_questions=likely_questions,
            star_themes=star_themes,
            gap_notes=gap_notes,
            questions_to_ask=questions_to_ask,
            reminders=reminders,
        )
        target_path.write_text(html_content)

        if export_pdf:
            # Reuse the resume tailor's hardened Playwright HTML→PDF path.
            ResumeTailor._export_pdf(html_content, target_path.with_suffix(".pdf"))

        result = InterviewPrepResult(
            output_path=target_path,
            fit_result=fit_result,
            opener=opener,
            likely_questions=likely_questions,
            star_themes=star_themes,
            gap_notes=gap_notes,
            questions_to_ask=questions_to_ask,
            reminders=reminders,
        )
        self._store_latest_manifest(result)
        return result

    # -- section builders --------------------------------------------------

    def _build_opener(self, profile: UserProfile, fit_result: JobFitResult) -> str:
        """A short 'tell me about yourself' scaffold. No invented facts, no year counts."""
        ai = self._maybe_ai_opener(profile, fit_result)
        if ai:
            return ai

        parsed = fit_result.parsed_jd
        role = parsed.title or profile.current_title or "this role"
        focus = ", ".join(fit_result.matched_skills[:3])
        bits: list[str] = []
        if profile.current_title:
            current = profile.current_title
            if profile.current_company:
                current = f"{current} at {profile.current_company}"
            bits.append(f"Currently: {current}.")
        if focus:
            bits.append(f"Directly relevant here: {focus}.")
        bits.append(
            f"Frame your background as a fit for {role} — lead with your most "
            "relevant role, then connect it to what this job needs."
        )
        return " ".join(bits)

    def _build_likely_questions(self, fit_result: JobFitResult) -> list[str]:
        parsed = fit_result.parsed_jd
        questions: list[str] = [
            "Tell me about yourself / walk me through your background.",
            f"Why this role, and why {parsed.company or 'this company'}?",
        ]

        for req in self._short_phrases(parsed.requirements)[:3]:
            questions.append(f"Walk me through your experience with: {req}.")

        for skill in fit_result.missing_skills[:2]:
            questions.append(f"How would you get up to speed on {skill}?")

        ai = self._maybe_ai_questions(fit_result)
        questions.extend(ai)

        # De-dupe (case-insensitive), keep order, cap the list.
        seen: set[str] = set()
        deduped: list[str] = []
        for q in questions:
            key = q.lower().strip()
            if key and key not in seen:
                seen.add(key)
                deduped.append(q.strip())
        return deduped[:9]

    def _build_gap_notes(self, profile: UserProfile, fit_result: JobFitResult) -> list[str]:
        notes: list[str] = []

        location_note = self._location_note(profile, fit_result)
        if location_note:
            notes.append(location_note)

        for skill in fit_result.missing_skills[:4]:
            notes.append(f"Be ready to address {skill} — how you'd ramp, or the closest thing you've done.")

        for risk in fit_result.risks[:3]:
            notes.append(risk)

        return notes

    def _build_questions_to_ask(self, fit_result: JobFitResult) -> list[str]:
        parsed = fit_result.parsed_jd
        questions = [
            "What separates someone who thrives in this role from someone who struggles?",
            "What does the first 90 days look like — what would I own?",
            "How is the team structured, and who would I work with most closely?",
        ]
        if parsed.location_type in {"onsite", "hybrid"}:
            questions.append("What does the location / on-site expectation actually look like day to day?")
        return questions

    def _build_reminders(self, profile: UserProfile, fit_result: JobFitResult) -> list[str]:
        reminders = [
            "Keep every answer truthful and specific — one real example beats three vague ones.",
            "End behavioral answers on the outcome, not the mechanics.",
            "Mirror the job's own language when it fits; don't force it.",
        ]
        if self._location_note(profile, fit_result):
            reminders.append(
                "Raise the location question yourself, near the end — after you've made your case, not before."
            )
        return reminders

    # -- location gate -----------------------------------------------------

    def _location_note(self, profile: UserProfile, fit_result: JobFitResult) -> str:
        """Flag a possible on-site/location mismatch so the user resolves it in the room.

        Reusable across candidates: fires when the role reads as on-site/hybrid
        and the candidate's own city isn't found in the JD text.
        """
        parsed = fit_result.parsed_jd
        if parsed.location_type not in {"onsite", "hybrid"}:
            return ""
        home = profile.city.strip()
        if not home:
            return ""
        haystack = " ".join([parsed.raw_text or "", parsed.summary()]).lower()
        if home.lower() in haystack:
            return ""
        where = f"{home}, {profile.state}".strip().strip(",") if profile.state else home
        return (
            f"This role reads as {parsed.location_type}. You're based in {where} — "
            "confirm exactly where the work sits and whether that's workable before you invest further."
        )

    # -- optional AI (never fabricates) ------------------------------------

    def _maybe_ai_opener(self, profile: UserProfile, fit_result: JobFitResult) -> str:
        if not self.use_bro or not llm_client.is_available():
            return ""
        parsed = fit_result.parsed_jd
        context = self._rag_context(parsed)
        prompt = (
            "Write a 2-3 sentence 'tell me about yourself' opener for a job interview. "
            "Use ONLY the facts given below. Do NOT invent employers, projects, or "
            "accomplishments. Do NOT state a number of years of experience. Plain text, no preamble.\n\n"
            f"Current title: {profile.current_title or 'N/A'}\n"
            f"Current company: {profile.current_company or 'N/A'}\n"
            f"Job target: {parsed.summary()}\n"
            f"Overlapping skills to emphasize: {', '.join(fit_result.matched_skills[:5]) or 'none listed'}\n"
        )
        try:
            reply = llm_client.complete(prompt, context=context or None, smart=True)
        except llm_client.LLMUnavailable as exc:
            log.debug("AI opener skipped, using template: %s", exc)
            return ""
        return " ".join(reply.split()).strip()

    def _maybe_ai_questions(self, fit_result: JobFitResult) -> list[str]:
        if not self.use_bro or not llm_client.is_available():
            return []
        parsed = fit_result.parsed_jd
        prompt = (
            "List 3 likely interview questions a hiring manager would ask for the role below. "
            "Base them on the role's requirements. Return one question per line, no numbering.\n\n"
            f"Job target: {parsed.summary()}\n"
            f"Key requirements: {'; '.join(self._short_phrases(parsed.requirements)[:5]) or 'n/a'}\n"
        )
        try:
            reply = llm_client.complete(prompt, smart=False)
        except llm_client.LLMUnavailable as exc:
            log.debug("AI questions skipped, using template: %s", exc)
            return []
        out: list[str] = []
        for line in reply.splitlines():
            stripped = line.strip().lstrip("-•*0123456789. ").strip()
            if stripped:
                out.append(stripped)
        return out[:3]

    @staticmethod
    def _rag_context(parsed) -> str:
        if not is_bro_running():
            return ""
        return query_rag(
            f"Candidate background relevant to {parsed.title or 'this role'} at {parsed.company or 'the company'}",
            top_k=4,
        )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _short_phrases(items: list[str]) -> list[str]:
        """Trim requirement lines into concise, question-able phrases."""
        phrases: list[str] = []
        for item in items:
            phrase = re.sub(r"\s+", " ", item).strip(" .;:-•*")
            if 3 <= len(phrase) <= 90:
                phrases.append(phrase)
        return list(dict.fromkeys(phrases))

    # -- render ------------------------------------------------------------

    def _render_html(
        self,
        profile: UserProfile,
        fit_result: JobFitResult,
        *,
        opener: str,
        likely_questions: list[str],
        star_themes: list[tuple[str, str]],
        gap_notes: list[str],
        questions_to_ask: list[str],
        reminders: list[str],
    ) -> str:
        parsed = fit_result.parsed_jd
        full_name = escape((f"{profile.first_name} {profile.last_name}").strip() or "Candidate")
        title = escape(parsed.title or "Target role")
        company = escape(parsed.company or "Target company")
        location = escape(parsed.location_type.title()) if parsed.location_type else ""

        questions_html = "".join(f"<li>{escape(q)}</li>" for q in likely_questions)
        star_html = "".join(
            f"<div class='card'><div class='card-h'>{escape(theme)}</div>"
            f"<div class='card-b'>{escape(hint)}</div></div>"
            for theme, hint in star_themes
        )
        gaps_html = "".join(f"<li>{escape(n)}</li>" for n in gap_notes)
        ask_html = "".join(f"<li>{escape(q)}</li>" for q in questions_to_ask)
        reminders_html = "".join(f"<li>{escape(r)}</li>" for r in reminders)
        meta_loc = f"<div class='meta-card'><div class='meta-label'>Location</div><div class='meta-value'>{location}</div></div>" if location else ""

        return f"""<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>{full_name} — Interview Brief: {title}</title>
  <style>
    :root {{
      --ink: #162033; --muted: #5c6880; --accent: #2356d8;
      --surface: #f5f7fb; --border: #d8dfec; --warn-bg: #fdf3dd; --warn: #8a5a00;
    }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; color: var(--ink); background: var(--surface); }}
    .page {{ max-width: 860px; margin: 24px auto; background: white; padding: 36px 42px; box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08); }}
    h1 {{ margin: 0; font-size: 26px; }}
    .subtitle {{ margin-top: 6px; font-size: 17px; color: var(--accent); font-weight: 600; }}
    .hero {{ border-bottom: 2px solid var(--border); padding-bottom: 16px; margin-bottom: 20px; }}
    h2 {{ font-size: 15px; text-transform: uppercase; letter-spacing: 0.08em; margin: 24px 0 10px; color: var(--accent); }}
    ul {{ margin: 8px 0 0 18px; padding: 0; }}
    li {{ margin: 7px 0; line-height: 1.45; }}
    .meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin: 10px 0 4px; }}
    .meta-card {{ border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; background: #fbfcff; }}
    .meta-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }}
    .meta-value {{ margin-top: 4px; font-weight: 600; }}
    .opener {{ border-left: 3px solid var(--accent); background: #f2f6ff; padding: 12px 16px; border-radius: 0 8px 8px 0; line-height: 1.5; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; }}
    .card {{ border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; background: #fbfcff; }}
    .card-h {{ font-weight: 700; margin-bottom: 4px; }}
    .card-b {{ color: var(--muted); font-size: 13px; line-height: 1.45; }}
    .gaps {{ background: var(--warn-bg); border-radius: 10px; padding: 4px 16px 12px; }}
    .gaps li {{ color: var(--warn); }}
    .note {{ color: var(--muted); font-size: 12px; margin-top: 26px; }}
    @media print {{
      body {{ background: white; }}
      .page {{ box-shadow: none; margin: 0; max-width: none; padding: 22px 26px; }}
    }}
  </style>
</head>
<body>
  <main class='page'>
    <section class='hero'>
      <h1>Interview Brief</h1>
      <div class='subtitle'>{title} &nbsp;·&nbsp; {company}</div>
      <div class='meta'>
        <div class='meta-card'><div class='meta-label'>Candidate</div><div class='meta-value'>{full_name}</div></div>
        <div class='meta-card'><div class='meta-label'>Fit Snapshot</div><div class='meta-value'>{fit_result.score}/100 — {escape(fit_result.recommendation)}</div></div>
        {meta_loc}
      </div>
    </section>

    <section>
      <h2>60-Second Opener</h2>
      <div class='opener'>{escape(opener)}</div>
    </section>

    <section>
      <h2>Likely Questions</h2>
      <ul>{questions_html}</ul>
    </section>

    <section>
      <h2>Stories to Prepare — anchor each to one real example, end on the outcome</h2>
      <div class='cards'>{star_html}</div>
    </section>

    {f"<section><h2>Get Ahead of These</h2><div class='gaps'><ul>{gaps_html}</ul></div></section>" if gaps_html else ""}

    <section>
      <h2>Ask Them</h2>
      <ul>{ask_html}</ul>
    </section>

    <section>
      <h2>Reminders</h2>
      <ul>{reminders_html}</ul>
    </section>

    <div class='note'>Generated by JobPilot from your profile and the job posting. Review before you rely on it.</div>
  </main>
</body>
</html>
"""

    # -- manifest & naming -------------------------------------------------

    def _store_latest_manifest(self, result: InterviewPrepResult) -> None:
        manifest_path = self.output_dir / LATEST_PREP_FILENAME
        parsed = result.fit_result.parsed_jd
        payload = {
            "html_path": str(result.output_path.expanduser().resolve()),
            "title": parsed.title or "",
            "company": parsed.company or "",
            "fit_score": result.fit_result.score,
            "recommendation": result.fit_result.recommendation,
            "question_count": len(result.likely_questions),
        }
        try:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(payload, indent=2))
        except Exception as exc:
            log.debug("Could not save latest prep manifest: %s", exc)

    def _default_output_path(self, fit_result: JobFitResult) -> Path:
        parsed = fit_result.parsed_jd
        stamp = hashlib.sha256((parsed.raw_text or parsed.summary()).encode()).hexdigest()[:8]
        company = self._slugify(parsed.company or "company")
        title = self._slugify(parsed.title or "role")
        return self.output_dir / f"prep_{company}_{title}_{stamp}.html"

    @staticmethod
    def _slugify(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug or "item"
