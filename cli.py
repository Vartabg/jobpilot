"""
JobPilot CLI — thin entry point.

Usage:
    jobpilot start       - Explain the retired live-form automation flow
    jobpilot profile     - View/edit your profile
    jobpilot templates   - Manage answer templates
    jobpilot stats       - Show application statistics
    jobpilot history     - Show recent application history
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

# Support both editable installs and repo-local execution from inside this folder.
_PROJECTS_DIR = Path(__file__).resolve().parent.parent
if str(_PROJECTS_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECTS_DIR))

import typer
from rich.console import Console
from rich.panel import Panel

from jobpilot.cli_agent import agent_app as agent_contract_app
from jobpilot.core import llm_client
from jobpilot.core.application_answerer import TRUE_ACCOUNTS_PATH, ApplicationAnswerer
from jobpilot.core.application_tracker import get_application_tracker
from jobpilot.core.bro_client import get_health
from jobpilot.core.cdp_bridge import connect_to_chrome
from jobpilot.core.config import DATA_DIR, resolve_data_path
from jobpilot.core.doctor import run_doctor
from jobpilot.core.interview_prep import InterviewPrepGenerator
from jobpilot.core.jd_parser import JDParser
from jobpilot.core.job_scorer import JobScorer
from jobpilot.core.logger import get_logger
from jobpilot.core.portal_scanner import PortalScanner, ScanTarget
from jobpilot.core.profile_store import get_profile_store
from jobpilot.core.question_matcher import get_question_matcher
from jobpilot.core.resume_tailor import ResumeTailor
from jobpilot.core.role_decision import RoleDecision, RoleDecisionEngine
from jobpilot.core.source_health import summarize_source_runs
from jobpilot.learning.action_recorder import get_action_recorder

# Named `logger`, not `log`: the `log` CLI command below would shadow it and
# any logger call would crash with AttributeError.
logger = get_logger(__name__)

app = typer.Typer(
    name="jobpilot",
    help="Evidence-led job search and application preparation assistant",
)
console = Console()
app.add_typer(agent_contract_app, name="agent")

# Legacy aliases remain patchable for integrations that predate the neutral
# target-review contract. New reports use agent-reviewed-targets-*.json.
CLAUDE_VETTED_TARGETS_DIR = DATA_DIR / "reports"
CLAUDE_VETTED_TARGETS_GLOB = "claude-vetted-targets-*.json"
CLAUDE_VETTED_TARGETS_PATH: Optional[Path] = None


def _resolve_claim_lock_path() -> Optional[Path]:
    """Return the newest model-neutral target-review report, if configured."""
    from jobpilot.core.target_review import resolve_target_review_path

    return resolve_target_review_path(
        directory=CLAUDE_VETTED_TARGETS_DIR,
        override=CLAUDE_VETTED_TARGETS_PATH,
    )


# ---------------------------------------------------------------------------
# Resume auto-index helper (unchanged)
# ---------------------------------------------------------------------------

def _check_and_index_resume() -> None:
    """Auto-index resume in RAG if configured and not yet indexed."""
    try:
        profile_store = get_profile_store()
        profile = profile_store.load()
        resume_path = profile.resume_path

        if not resume_path:
            return

        import sys
        from pathlib import Path

        resume = Path(resume_path).expanduser()
        if not resume.exists():
            console.print(f"[yellow]Resume not found: {resume_path}[/yellow]")
            return

        health = get_health()
        if health.get("rag_chunks", 0) > 0:
            console.print(f"[dim]RAG has {health['rag_chunks']} chunks indexed[/dim]")
            return

        rag_dir = os.environ.get("JOBPILOT_RAG_DIR")
        if not rag_dir:
            console.print("[dim]RAG indexing skipped: set JOBPILOT_RAG_DIR to enable[/dim]")
            return

        rag_path = Path(rag_dir)
        if not rag_path.exists():
            console.print(f"[yellow]RAG directory not found: {rag_dir}[/yellow]")
            return

        console.print(f"[cyan]Indexing resume: {resume.name}...[/cyan]")
        import subprocess
        result = subprocess.run(
            [sys.executable, "main.py", "index", str(resume)],
            cwd=str(rag_path),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            console.print("[green]✓ Resume indexed in RAG[/green]")
        else:
            console.print(f"[yellow]Resume indexing issue: {result.stderr[:100]}[/yellow]")
    except Exception as e:
        console.print(f"[dim]Resume indexing skipped: {e}[/dim]")


def _load_score_source(source: str) -> tuple[str, str]:
    """Resolve inline JD text or a file path into scoreable text."""
    candidate = Path(source).expanduser()
    try:
        is_file = candidate.is_file()
    except OSError:
        # Long pasted JDs are inline text, but some filesystems reject them
        # before ``Path.is_file`` can simply return false.
        is_file = False
    if is_file:
        if candidate.suffix.lower() == ".pdf":
            console.print(
                "[red]That's a PDF — JobPilot can't read job descriptions out of PDFs yet.[/red]\n"
                "[yellow]Copy the job description into a .txt file, or pass a URL instead.[/yellow]"
            )
            raise typer.Exit(1)
        try:
            return candidate.read_text(), str(candidate)
        except UnicodeDecodeError as exc:
            console.print(
                "[red]That looks like a binary file — paste the job description "
                "into a .txt file, or pass a URL.[/red]"
            )
            raise typer.Exit(1) from exc

    # The source doesn't exist as a file. If it *looks* like a path (a single
    # token with a doc suffix or a path separator), the user almost certainly
    # meant a file and mistyped it. Fail loudly instead of silently treating the
    # path string as the job description — that quietly produces a wrong resume.
    if _looks_like_path(source):
        console.print(
            f"[red]No such file:[/red] {source}\n"
            "[yellow]Check the path, or paste the job description text directly.[/yellow]"
        )
        raise typer.Exit(1)

    return source, "inline text"


def _looks_like_path(source: str) -> bool:
    """Heuristic: does this string look like a (mistyped) file path, not JD prose?

    Real pasted job descriptions are multi-word prose with whitespace. A path is
    a single token, often with a known document suffix or a separator.
    """
    text = source.strip()
    if not text or "\n" in text or len(text) > 250:
        return False
    has_doc_suffix = Path(text).suffix.lower() in {".txt", ".md", ".rst", ".pdf"}
    has_separator = "/" in text or "\\" in text
    is_single_token = " " not in text
    return has_doc_suffix or (has_separator and is_single_token)


def _parse_years_of_experience(raw: str) -> Optional[int]:
    """Parse a years-of-experience answer ('5', '5 years', '12+ yrs') into an int.

    Returns None when no leading number can be found.
    """
    match = re.match(r"(\d+)", str(raw or "").strip())
    return int(match.group(1)) if match else None


def _parse_csv_list(raw: str) -> list[str]:
    """Parse a friendly comma/semicolon/newline list without duplicates."""
    values = (part.strip() for part in re.split(r"[,;\n]", str(raw or "")))
    return list(dict.fromkeys(part for part in values if part))


def _parse_optional_yes_no(raw: str) -> Optional[bool]:
    """Parse an explicit yes/no answer, leaving blank or unknown unset."""
    value = str(raw or "").strip().lower()
    if value in {"y", "yes", "true", "1"}:
        return True
    if value in {"n", "no", "false", "0"}:
        return False
    return None


def _norm_claim_text(value: object) -> str:
    from jobpilot.core.target_review import normalize_review_text

    return normalize_review_text(value)


def _load_claim_targets(lock_path: Path) -> list[dict]:
    from jobpilot.core.target_review import load_review_targets

    return load_review_targets(lock_path)


def _find_claim_target(job, lock_path: Path) -> Optional[dict]:
    from jobpilot.core.target_review import find_review_target

    return find_review_target(job, lock_path)


def _claim_state_for_job(job) -> str:
    """Expose claim-lock readiness without copying it into queue.json."""
    from jobpilot.core.target_review import review_state_for_job

    review_path = _resolve_claim_lock_path()
    return (
        review_state_for_job(job, review_path=review_path)
        if review_path is not None
        else "unconfigured"
    )


def _job_output_payload(job) -> dict:
    """Build an everyday jobs payload with live claim-lock state."""
    from jobpilot.core.agent_contract import opportunity_payload
    from jobpilot.core.queue_builder import QueueJob, is_apply_ready

    ready = is_apply_ready(job) if isinstance(job, QueueJob) else False
    return opportunity_payload(
        job,
        ready=ready,
        review_state=_claim_state_for_job(job),
    )


def _enforce_claim_lock(job, *, claim_approved: bool) -> None:
    lock_path = _resolve_claim_lock_path()
    if lock_path is None:
        console.print("[dim]Claim-lock not configured (no vetted-targets file) — skipping that check.[/dim]")
        return

    try:
        target = _find_claim_target(job, lock_path)
    except RuntimeError as exc:
        console.print(f"[red]Claim-lock blocked staging:[/red] {exc}")
        raise typer.Exit(1) from exc

    if target is None:
        console.print(
            "[red]Claim-lock blocked staging:[/red] "
            f"{job.company} is not present in {lock_path}."
        )
        console.print("[dim]An authorized agent must review it and set materials_status to 'ready' before JobPilot prepares it.[/dim]")
        raise typer.Exit(1)

    decision = _norm_claim_text(target.get("decision"))
    materials_status = _norm_claim_text(target.get("materials_status"))
    if decision != "keep":
        console.print(
            "[red]Claim-lock blocked staging:[/red] "
            f"{job.company} is marked decision={target.get('decision')!r}."
        )
        raise typer.Exit(1)
    if materials_status != "ready":
        console.print(
            "[red]Claim-lock blocked staging:[/red] "
            f"{job.company} materials_status={target.get('materials_status')!r}; expected 'ready'."
        )
        raise typer.Exit(1)
    if not claim_approved:
        approved = typer.confirm(
            f"Reviewed materials are ready for {job.company}. Approve staging this target now?",
            default=False,
        )
        if not approved:
            console.print("[dim]Not staged. Waiting for your explicit approval.[/dim]")
            raise typer.Exit(0)


def _render_score_result(
    result: RoleDecision,
    source_label: str,
    *,
    title: str = "",
    company: str = "",
) -> None:
    """Display a truth-bounded role decision with its evidence axes."""
    from rich.table import Table

    decision_style = {
        "apply_now": "green",
        "stretch": "cyan",
        "investigate": "yellow",
        "skip": "red",
    }.get(result.decision, "yellow")
    title = title or "Role evidence review"
    company = company or "Company not identified"
    status = result.status.replace("_", " ")
    decision = result.decision.replace("_", " ")

    console.print(
        Panel.fit(
            f"[bold cyan]{title}[/bold cyan]\n"
            f"[white]{company}[/white]\n"
            f"Status: [bold]{status}[/bold]  •  "
            f"Role-fit decision: "
            f"[bold {decision_style}]{decision}[/bold {decision_style}]\n"
            f"[dim]{source_label}[/dim]",
            border_style=decision_style,
            title="Evidence-linked role decision",
        )
    )

    def axis(value) -> str:
        return "unknown" if value is None else f"{value}/100"

    matched_accounts = ", ".join(result.matched_accounts) or "none linked"
    table = Table(title="Evidence axes")
    table.add_column("Evidence", style="cyan")
    table.add_column("Finding", style="white")
    table.add_row("Role family", result.role_family.replace("_", " "))
    table.add_row("Qualification lower bound", axis(result.qualification_lower_bound))
    table.add_row("Work-context evidence", axis(result.work_context_match))
    table.add_row("Evidence coverage", f"{result.evidence_coverage}%")
    table.add_row("Opportunity evidence", axis(result.opportunity))
    table.add_row("Logistics evidence", axis(result.logistics))
    table.add_row("Matched accounts", matched_accounts)
    table.add_row("Biggest gap", result.biggest_gap or "none recorded")
    console.print(table)
    console.print(
        "[yellow]Fit only — not Apply-ready.[/yellow] Current posting-source, "
        "application-history, and ledger checks still must pass in "
        "[cyan]jobpilot queue --refresh[/cyan]."
    )

    if result.status == "unscorable":
        console.print(
            "[yellow]Unscorable:[/yellow] a full job description is required; "
            "unknown evidence received no neutral points."
        )
    if result.rationale:
        console.print(f"[dim]{result.rationale}[/dim]")


_SCORE_SECTION_HEADINGS = frozenset(
    {
        "about the role",
        "about us",
        "logistics",
        "preferred qualifications",
        "requirements",
        "required qualifications",
        "responsibilities",
        "work context",
        "work environment",
    }
)


def _score_identity(raw_text: str) -> tuple[str, str]:
    """Infer the role identity without invoking the legacy additive scorer."""
    labels = {
        "company": "company",
        "employer": "company",
        "organization": "company",
        "job title": "title",
        "position": "title",
        "role": "title",
        "title": "title",
    }
    found = {"title": "", "company": ""}
    candidates = []
    for raw_line in (raw_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        label, separator, value = line.partition(":")
        field = labels.get(label.strip().lower()) if separator else None
        if field and value.strip():
            found[field] = found[field] or value.strip()[:120]
            continue
        if (
            line.lower().rstrip(":") not in _SCORE_SECTION_HEADINGS
            and not line.startswith(("-", "*", "•"))
            and 3 <= len(line) <= 120
        ):
            candidates.append(line)

    if not found["title"] and candidates:
        found["title"] = candidates[0]
    if not found["company"] and len(candidates) > 1:
        found["company"] = candidates[1]
    return found["title"], found["company"]


def _missing_jd_decision() -> RoleDecision:
    """Return an explicit fail-closed decision when no JD body was captured."""
    return RoleDecision(
        status="unscorable",
        decision="investigate",
        role_family="unknown",
        qualification_lower_bound=None,
        work_context_match=None,
        opportunity=None,
        logistics=None,
        evidence_coverage=0,
        biggest_gap="A full job description is required.",
        matched_accounts=(),
        matches=(),
        rationale=("Missing job-description evidence; no neutral points were awarded."),
    )


def _assess_score_text(*, title: str, jd_text: str) -> RoleDecision:
    """Assess public score input against the current truth-bounded profile."""
    if not (jd_text or "").strip():
        return _missing_jd_decision()
    accounts_path = TRUE_ACCOUNTS_PATH if TRUE_ACCOUNTS_PATH.is_file() else None
    return RoleDecisionEngine().assess(
        title=title,
        jd_text=jd_text,
        profile=get_profile_store().load(),
        accounts_path=accounts_path,
    )


def _render_resume_result(result, source_label: str) -> None:
    """Display a tailored draft with the current evidence-based decision."""
    parsed = result.fit_result.parsed_jd
    decision = _assess_score_text(title=parsed.title, jd_text=parsed.raw_text)
    decision_label = decision.decision.replace("_", " ")
    qualification_floor = (
        "unknown"
        if decision.qualification_lower_bound is None
        else f"{decision.qualification_lower_bound}/100"
    )
    saved_paths = [f"Markdown: {result.output_path}"]
    if result.html_path:
        saved_paths.append(f"HTML: {result.html_path}")
    if result.pdf_path:
        saved_paths.append(f"PDF: {result.pdf_path}")

    console.print(Panel.fit(
        f"[bold cyan]ATS Resume Draft Ready[/bold cyan]\n"
        f"[white]{parsed.title or 'Target role'} @ {parsed.company or 'Target company'}[/white]\n"
        f"Role-fit decision: [bold]{decision_label}[/bold]\n"
        f"Qualification floor: {qualification_floor}  •  "
        f"Evidence coverage: {decision.evidence_coverage}%\n"
        f"[dim]{source_label}\n" + "\n".join(saved_paths) + "[/dim]",
        border_style="cyan",
    ))

    if decision.biggest_gap:
        console.print(f"[yellow]Biggest gap:[/yellow] {decision.biggest_gap}")
    console.print(
        "[yellow]Fit only — not Apply-ready.[/yellow] Refresh the queue to verify "
        "the posting source, application history, and ledger evidence."
    )

    if result.keywords:
        console.print(f"[green]Keywords:[/green] {', '.join(result.keywords[:10])}")
    for line in result.summary_lines[:3]:
        console.print(f"[green]•[/green] {line}")


_BOILERPLATE_PHRASES = (
    "we are an equal opportunity employer",
    "equal opportunity",
    "we offer competitive",
    "join our team",
    "about the company",
    "about us",
    "who we are",
    "our mission",
    "we believe in",
    "we are committed to",
    "drug test",
    "background check",
    "we look forward to",
    "apply now",
    "click here to apply",
)

_DUTY_MARKERS = (
    "what you'll do",
    "what you will do",
    "responsibilities",
    "your role",
    "in this role",
    "you will",
    "key duties",
    "day-to-day",
    "day to day",
    "essential duties",
    "position summary",
    "job summary",
)


def _format_description(raw: str) -> str:
    """Extract and clean the duties section from a raw job description.

    No API calls — uses local heuristics to find the relevant section,
    strip boilerplate, and format bullet points readably.
    """
    if not raw:
        return ""

    # Find where the actual duties start.
    lower = raw.lower()
    best_pos = 0
    for marker in _DUTY_MARKERS:
        pos = lower.find(marker)
        if pos != -1 and (best_pos == 0 or pos < best_pos):
            best_pos = pos

    text = raw[best_pos:].strip() if best_pos else raw.strip()

    # Split into lines and filter out boilerplate lines.
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower_line = stripped.lower()
        if any(phrase in lower_line for phrase in _BOILERPLATE_PHRASES):
            continue
        # Normalise bullet characters to a consistent "•"
        if stripped.startswith(("- ", "* ", "· ", "• ")):
            stripped = "• " + stripped[2:]
        lines.append(stripped)

    return "\n".join(lines)


def _render_scan_results(jobs, *, limit: int = 20, describe: bool = False) -> None:
    """Display scanned ATS matches — table view or descriptive card view."""
    if not jobs:
        console.print("[yellow]No matching jobs found.[/yellow]")
        return

    shown = jobs[:limit]

    if describe:
        console.print(
            f"[bold cyan]Portal Matches[/bold cyan] "
            f"[dim]({len(shown)} shown / {len(jobs)} total)[/dim]\n"
        )
        for i, job in enumerate(shown, 1):
            header = (
                f"[dim]{i}[/dim]  "
                f"[bold green]{job.company}[/bold green] [dim]·[/dim] [bold]{job.title}[/bold]"
            )
            loc_line = f"[magenta]{job.location}[/magenta]  [dim]via {job.portal}[/dim]"
            blurb = _format_description(job.description) if job.description else ""
            body = f"{loc_line}\n\n{blurb}" if blurb else f"{loc_line}\n\n[dim italic]No description available.[/dim italic]"
            console.print(Panel(body, title=header, title_align="left", padding=(0, 1)))
        return

    from rich.table import Table

    table = Table(title=f"Portal Matches ({len(shown)} shown / {len(jobs)} total)")
    table.add_column("Portal", style="cyan", width=10)
    table.add_column("Company", style="white", max_width=20)
    table.add_column("Role", style="green", max_width=34)
    table.add_column("Location", style="magenta", max_width=18)
    table.add_column("Match", style="yellow", max_width=18)

    for job in shown:
        table.add_row(
            job.portal,
            job.company or "—",
            job.title or "—",
            job.location or "—",
            ", ".join(job.matched_keywords[:3]) or "—",
        )

    console.print(table)


def _render_doctor_report(report) -> None:
    """Display the result of a JobPilot health check."""
    from rich.table import Table

    color = {"ok": "green", "warn": "yellow", "error": "red"}.get(report.status, "white")
    console.print(Panel.fit(
        f"[bold {color}]🩺 JobPilot Doctor: {report.status.upper()}[/bold {color}]\n"
        f"[dim]{report.summary.get('data_dir', '')}[/dim]",
        border_style=color,
    ))

    table = Table(title="Health Summary")
    table.add_column("Check", style="cyan")
    table.add_column("Value", justify="right", style="white")
    for key, value in report.summary.items():
        if key == "data_dir":
            continue
        table.add_row(key.replace("_", " ").title(), str(value))
    console.print(table)

    for item in report.infos[:6]:
        console.print(f"[green]•[/green] {item}")
    for item in report.warnings[:6]:
        console.print(f"[yellow]•[/yellow] {item}")
    for item in report.errors[:6]:
        console.print(f"[red]•[/red] {item}")


async def _resume_active_page(
    port: int,
    *,
    output: Optional[Path],
    use_bro: bool,
    export_html: bool,
    export_pdf: bool,
) -> bool:
    """Generate a tailored resume draft from the active LinkedIn job page."""
    console.print("\n[cyan]Connecting to Chrome for active resume tailoring...[/cyan]")
    bridge = await connect_to_chrome(port)

    if not bridge:
        console.print("\n[yellow]💡 Tip: Run ./scripts/launch_chrome.sh first[/yellow]")
        return False

    try:
        await bridge.get_active_page()
        page_info = await bridge.get_page_info()
        if not page_info.is_linkedin:
            console.print("[yellow]Open a LinkedIn job listing first, or pass JD text/file directly.[/yellow]")
            return False

        parsed_jd = await JDParser(bridge.page).parse()
    finally:
        await bridge.disconnect()

    if not parsed_jd or not (parsed_jd.raw_text or parsed_jd.summary()):
        console.print("[yellow]Could not read the active job description.[/yellow]")
        return False

    tailor = ResumeTailor(use_bro=use_bro)
    fit_result = JobScorer(use_bro=use_bro).score_parsed_jd(parsed_jd)
    result = tailor.generate_from_fit_result(
        fit_result,
        output_path=output,
        export_html=export_html or export_pdf,
        export_pdf=export_pdf,
    )
    _render_resume_result(result, page_info.url)
    return True


async def _score_active_page(port: int) -> bool:
    """Assess the currently open LinkedIn role from its full JD evidence."""
    console.print("\n[cyan]Connecting to Chrome for active role evidence...[/cyan]")
    bridge = await connect_to_chrome(port)

    if not bridge:
        console.print("\n[yellow]💡 Tip: Run ./scripts/launch_chrome.sh first[/yellow]")
        return False

    try:
        await bridge.get_active_page()
        page_info = await bridge.get_page_info()
        if not page_info.is_linkedin:
            console.print(
                "[yellow]Open a LinkedIn job listing first, or pass JD text/file directly.[/yellow]"
            )
            return False

        parsed_jd = await JDParser(bridge.page).parse()
    finally:
        await bridge.disconnect()

    if parsed_jd is None:
        result = _missing_jd_decision()
        title = getattr(page_info, "title", "")
        company = ""
    else:
        inferred_title, inferred_company = _score_identity(parsed_jd.raw_text)
        title = parsed_jd.title or inferred_title
        company = parsed_jd.company or inferred_company
        result = _assess_score_text(title=title, jd_text=parsed_jd.raw_text)
    _render_score_result(
        result,
        page_info.url,
        title=title,
        company=company,
    )
    return True


async def _doctor_async(port: int, *, bro: bool = True) -> int:
    """Check whether JobPilot can reach its core runtime dependencies."""
    from rich.table import Table

    bro_ok = True
    whisper_ready = False
    models: list[str] = []
    preferred_fast = ""
    fast_ready = False

    if bro:
        health = get_health()
        bro_ok = health.get("status") == "ok"
        whisper_ready = health.get("whisper") == "ready"
        models = [str(model) for model in health.get("ollama_models", [])]
        preferred_fast = str(health.get("fast_model", "") or "").strip()
        fast_ready = bool(preferred_fast and any(preferred_fast in model for model in models))

    table = Table(title="JobPilot Doctor")
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="white")
    table.add_column("Details", style="dim")

    gemini_key_set = bool(os.environ.get(llm_client.GEMINI_API_KEY_ENV, "").strip())
    ai_ok = bro_ok or gemini_key_set

    if bro:
        if bro_ok:
            ai_detail = "Local Bro server reachable"
        elif gemini_key_set:
            ai_detail = "Gemini API key configured"
        else:
            ai_detail = (
                "No AI backend — set GEMINI_API_KEY "
                "(free tier: https://aistudio.google.com/app/apikey) "
                "or start the local Bro stack"
            )
        table.add_row(
            "AI backend",
            "OK" if ai_ok else "WARN",
            ai_detail,
        )
        table.add_row(
            "Whisper",
            "OK" if whisper_ready else "WARN",
            "Local STT ready" if whisper_ready else "Speech transcription unavailable",
        )

        fast_status = "OK" if fast_ready else "INFO"
        if fast_ready:
            fast_detail = f"Fast local model detected: {preferred_fast}"
        elif models:
            fast_detail = f"Bro reports models: {', '.join(models[:3])}"
        else:
            fast_detail = "No explicit fast model reported; optional optimization only"

        table.add_row(
            "Fast model",
            fast_status,
            fast_detail,
        )

    bridge = await connect_to_chrome(port)
    chrome_ok = bridge is not None
    linkedin_detail = "Not checked"

    if bridge:
        try:
            await bridge.get_active_page()
            page_info = await bridge.get_page_info()
            if page_info.is_linkedin:
                linkedin_detail = f"LinkedIn tab ready — {page_info.title[:60]}"
            else:
                linkedin_detail = f"Active tab is not LinkedIn — {page_info.url[:60]}"
        finally:
            await bridge.disconnect()

    table.add_row(
        "Chrome CDP",
        "OK" if chrome_ok else "FAIL",
        "Connected to existing Chrome session" if chrome_ok else f"Could not connect on port {port}",
    )

    linkedin_status = "OK" if chrome_ok and linkedin_detail.startswith("LinkedIn") else "INFO" if chrome_ok else "FAIL"
    if chrome_ok and not linkedin_detail.startswith("LinkedIn"):
        linkedin_detail = f"{linkedin_detail} — open a LinkedIn job tab when you want pre-apply checks"

    table.add_row(
        "LinkedIn",
        linkedin_status,
        linkedin_detail,
    )

    console.print(table)

    if not chrome_ok:
        console.print("\n[yellow]💡 Tip: Run ./scripts/launch_chrome.sh and reopen the target job tab.[/yellow]")
        return 1

    if bro and not ai_ok:
        console.print(
            "\n[yellow]No AI backend configured — chat, tailoring, and advice fall back to "
            "templates. Set GEMINI_API_KEY (free tier: https://aistudio.google.com/app/apikey) "
            "or start the local Bro stack.[/yellow]"
        )

    console.print("\n[green]✓ JobPilot runtime looks ready.[/green]")
    return 0


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def start(
    port: int = typer.Option(
        9222, help="Retained for compatibility; no browser is opened"
    ),
    watch: bool = typer.Option(
        True, help="Retained for compatibility; monitoring is retired"
    ),
    mode: str = typer.Option(
        "semi-auto", help="Retained for compatibility; automation is retired"
    ),
):
    """Explain the retired browser-monitoring flow and stop safely."""
    console.print(
        Panel.fit(
            "[bold yellow]`jobpilot start` is retired.[/bold yellow]\n"
            "JobPilot no longer monitors Chrome or fills live ATS forms.\n\n"
            "Use [cyan]jobpilot queue --refresh[/cyan] to verify roles, "
            "[cyan]jobpilot score <jd.txt>[/cyan] to review evidence, then "
            "draft a paste sheet and paste, review, and submit by hand.",
            title="Human paste-and-submit boundary",
            border_style="yellow",
        )
    )
    raise typer.Exit(1)


async def _start_async(port: int, watch: bool):
    """Fail closed for callers that retained the former private entry point."""
    del port, watch
    raise RuntimeError(
        "Live ATS monitoring and filling are retired; use the human paste flow."
    )


@app.command()
def scan(
    greenhouse: Optional[list[str]] = typer.Option(None, "--greenhouse", help="Greenhouse board token; repeat for multiple."),
    lever: Optional[list[str]] = typer.Option(None, "--lever", help="Lever site token; repeat for multiple."),
    ashby: Optional[list[str]] = typer.Option(None, "--ashby", help="Ashby org slug; repeat for multiple."),
    keyword: Optional[list[str]] = typer.Option(None, "--keyword", "-k", help="Keyword filter for titles or locations."),
    config: Optional[Path] = typer.Option(None, "--config", help="Optional JSON file of scan targets."),
    limit: int = typer.Option(20, help="Maximum number of rows to show"),
    save: bool = typer.Option(True, "--save/--no-save", help="Persist scan results to `data/reports/`"),
    describe: bool = typer.Option(False, "--describe", "-d", help="Show a plain-English description of what each role involves."),
):
    """Scan public ATS boards for roles worth reviewing before you apply."""
    targets: list[ScanTarget] = []
    for token in greenhouse or []:
        targets.append(ScanTarget(portal="greenhouse", value=token))
    for token in lever or []:
        targets.append(ScanTarget(portal="lever", value=token))
    for token in ashby or []:
        targets.append(ScanTarget(portal="ashby", value=token))

    if not targets:
        targets = PortalScanner.load_targets(config)

    if not targets:
        console.print(
            "[yellow]No scan targets configured. Use `--greenhouse anthropic`, `--lever company`, or add `data/portals.json`.[/yellow]"
        )
        raise typer.Exit(1)

    scanner = PortalScanner(keywords=keyword)
    jobs = scanner.scan_targets(targets)
    source_health = summarize_source_runs(scanner.last_run_results)
    jobs.sort(key=lambda item: (item.company.lower(), item.title.lower()))
    _render_scan_results(jobs, limit=limit, describe=describe)
    console.print(f"[dim]{source_health.summary_line}[/dim]")
    for notice in source_health.notices:
        console.print(f"[yellow]Source attention:[/yellow] {notice}")

    if save:
        report_path = scanner.save_report(jobs)
        console.print(f"[green]✓ Saved scan report to {report_path}[/green]")


@app.command()
def doctor(
    port: int = typer.Option(9222, help="Chrome debugging port"),
    json_output: bool = typer.Option(False, "--json", help="Output the health report as JSON"),
    strict: bool = typer.Option(False, "--strict", help="Exit non-zero if warnings are present"),
    bro: bool = typer.Option(True, "--bro/--no-bro", help="Include the AI backend health check"),
):
    """Check Chrome, the AI backend, and JobPilot's local data health."""
    runtime_exit = asyncio.run(_doctor_async(port, bro=bro))
    report = run_doctor(check_bro=bro)

    if json_output:
        console.print(json.dumps(report.to_dict(), indent=2))
    else:
        _render_doctor_report(report)

    if runtime_exit or report.errors or (strict and report.warnings):
        raise typer.Exit(1)


@app.command()
def resume(
    source: Optional[str] = typer.Argument(
        None,
        help="Job description text or a path to a JD text file. Omit to use the active LinkedIn page.",
    ),
    port: int = typer.Option(9222, help="Chrome debugging port for --active mode"),
    active: bool = typer.Option(False, "--active", help="Use the active LinkedIn job tab in Chrome"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Optional markdown output path for the resume draft"),
    html: bool = typer.Option(True, "--html/--no-html", help="Also write a styled HTML version next to the markdown draft"),
    pdf: bool = typer.Option(False, "--pdf", help="Also export a PDF version using Playwright/Chromium"),
    bro: bool = typer.Option(
        False,
        "--bro/--no-bro",
        help="Retired compatibility flag; resume generation remains model-free.",
    ),
):
    """Generate an ATS-friendly resume draft tailored to a target role."""
    if bro:
        console.print(
            "[yellow]Resume AI is retired.[/yellow] The draft will remain "
            "deterministic and evidence-only."
        )
    if active or not source:
        if not asyncio.run(
            _resume_active_page(
                port,
                output=output,
                use_bro=False,
                export_html=html,
                export_pdf=pdf,
            )
        ):
            raise typer.Exit(1)
        return

    if source.startswith(("http://", "https://")):
        console.print("[yellow]Open the job in Chrome and run `jobpilot resume --active`, or paste the JD text directly.[/yellow]")
        raise typer.Exit(1)

    text, label = _load_score_source(source)
    tailor = ResumeTailor(use_bro=False)
    result = tailor.generate_from_text(
        text,
        output_path=output,
        export_html=html or pdf,
        export_pdf=pdf,
    )
    _render_resume_result(result, label)


def _render_prep_result(result, source_label: str) -> None:
    """Display an interview brief with the current evidence-based decision."""
    parsed = result.fit_result.parsed_jd
    decision = _assess_score_text(title=parsed.title, jd_text=parsed.raw_text)
    decision_label = decision.decision.replace("_", " ")
    qualification_floor = (
        "unknown"
        if decision.qualification_lower_bound is None
        else f"{decision.qualification_lower_bound}/100"
    )
    console.print(Panel.fit(
        f"[bold cyan]Interview Brief Ready[/bold cyan]\n"
        f"[white]{parsed.title or 'Target role'} @ {parsed.company or 'Target company'}[/white]\n"
        f"Role-fit decision: [bold]{decision_label}[/bold]\n"
        f"Qualification floor: {qualification_floor}  •  "
        f"Evidence coverage: {decision.evidence_coverage}%\n"
        f"[dim]{source_label}\nHTML: {result.output_path}[/dim]",
        border_style="cyan",
    ))
    if decision.biggest_gap:
        console.print(f"[yellow]Biggest gap:[/yellow] {decision.biggest_gap}")
    console.print(
        "[yellow]Fit only — not Apply-ready.[/yellow] Refresh the queue to verify "
        "the posting source, application history, and ledger evidence."
    )
    if result.likely_questions:
        console.print("[green]Likely questions:[/green]")
        for q in result.likely_questions[:3]:
            console.print(f"  [green]•[/green] {q}")
    for note in result.gap_notes[:2]:
        console.print(f"[yellow]⚠ {note}[/yellow]")


async def _prep_active_page(
    port: int,
    *,
    output: Optional[Path],
    use_bro: bool,
    export_pdf: bool,
) -> bool:
    """Generate an interview brief from the active LinkedIn job page."""
    console.print("\n[cyan]Connecting to Chrome for interview prep...[/cyan]")
    bridge = await connect_to_chrome(port)

    if not bridge:
        console.print("\n[yellow]💡 Tip: Run ./scripts/launch_chrome.sh first[/yellow]")
        return False

    try:
        await bridge.get_active_page()
        page_info = await bridge.get_page_info()
        if not page_info.is_linkedin:
            console.print("[yellow]Open a LinkedIn job listing first, or pass JD text/file directly.[/yellow]")
            return False

        parsed_jd = await JDParser(bridge.page).parse()
    finally:
        await bridge.disconnect()

    if not parsed_jd or not (parsed_jd.raw_text or parsed_jd.summary()):
        console.print("[yellow]Could not read the active job description.[/yellow]")
        return False

    fit_result = JobScorer(use_bro=use_bro).score_parsed_jd(parsed_jd)
    generator = InterviewPrepGenerator(use_bro=use_bro)
    result = generator.generate_from_fit_result(fit_result, output_path=output, export_pdf=export_pdf)
    _render_prep_result(result, page_info.url)
    return True


@app.command()
def prep(
    source: Optional[str] = typer.Argument(
        None,
        help="Job description text or a path to a JD text file. Omit to use the active LinkedIn page.",
    ),
    port: int = typer.Option(9222, help="Chrome debugging port for --active mode"),
    active: bool = typer.Option(False, "--active", help="Use the active LinkedIn job tab in Chrome"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Optional HTML output path for the brief"),
    pdf: bool = typer.Option(False, "--pdf", help="Also export a PDF version using Playwright/Chromium"),
    bro: bool = typer.Option(True, "--bro/--no-bro", help="Use the AI backend (local Bro or Gemini) to sharpen the brief when available"),
):
    """Generate a one-page interview prep brief for a target role."""
    if active or not source:
        if not asyncio.run(
            _prep_active_page(port, output=output, use_bro=bro, export_pdf=pdf)
        ):
            raise typer.Exit(1)
        return

    if source.startswith(("http://", "https://")):
        console.print("[yellow]Open the job in Chrome and run `jobpilot prep --active`, or paste the JD text directly.[/yellow]")
        raise typer.Exit(1)

    text, label = _load_score_source(source)
    generator = InterviewPrepGenerator(use_bro=bro)
    result = generator.generate_from_text(text, output_path=output, export_pdf=pdf)
    _render_prep_result(result, label)


@app.command()
def score(
    source: Optional[str] = typer.Argument(
        None,
        help="Job description text or a path to a JD text file. Omit to score the active LinkedIn page.",
    ),
    port: int = typer.Option(9222, help="Chrome debugging port for --active mode"),
    active: bool = typer.Option(
        False, "--active", help="Score the active LinkedIn job tab in Chrome"
    ),
):
    """Review a role using profile-linked evidence and explicit unknowns."""
    if active or not source:
        if not asyncio.run(_score_active_page(port)):
            raise typer.Exit(1)
        return

    if source.startswith(("http://", "https://")):
        console.print(
            "[yellow]Open the job in Chrome and run `jobpilot score --active`, or paste the JD text directly.[/yellow]"
        )
        raise typer.Exit(1)

    text, label = _load_score_source(source)
    title, company = _score_identity(text)
    result = _assess_score_text(title=title, jd_text=text)
    _render_score_result(result, label, title=title, company=company)


@app.command()
def profile(
    edit: bool = typer.Option(False, "--edit", "-e", help="Edit profile interactively"),
):
    """View or edit your profile data"""
    store = get_profile_store()
    if edit:
        _edit_profile(store)
    else:
        store.display()


def _edit_profile(store):
    """Interactive profile editing"""
    p = store.load()

    console.print("\n[cyan]Edit your profile[/cyan] (press Enter to keep current value)\n")

    p.first_name = typer.prompt("First name", default=p.first_name or "")
    p.last_name = typer.prompt("Last name", default=p.last_name or "")
    p.email = typer.prompt("Email", default=p.email or "")
    p.phone = typer.prompt("Phone", default=p.phone or "")
    p.city = typer.prompt("City", default=p.city or "")
    p.state = typer.prompt("State", default=p.state or "")
    p.country = typer.prompt(
        "Country (leave blank if unknown)",
        default=p.country or "",
        show_default=False,
    )
    while True:
        relocation = typer.prompt(
            "Open to relocation for a confirmed role? (yes/no, blank if unknown)",
            default=(
                "yes" if p.open_to_relocation is True
                else "no" if p.open_to_relocation is False
                else ""
            ),
            show_default=False,
        )
        parsed_relocation = _parse_optional_yes_no(relocation)
        if not relocation.strip() or parsed_relocation is not None:
            p.open_to_relocation = parsed_relocation
            break
        console.print("[yellow]Enter yes, no, or leave it blank.[/yellow]")
    p.linkedin_url = typer.prompt("LinkedIn URL", default=p.linkedin_url or "")
    p.portfolio_url = typer.prompt("Portfolio URL", default=p.portfolio_url or "")
    p.github_url = typer.prompt("GitHub URL", default=p.github_url or "")
    p.resume_path = typer.prompt("Resume file path", default=p.resume_path or "")
    while True:
        authorized = typer.prompt(
            "Authorized to work in the United States? (yes/no, blank if unknown)",
            default=(
                "yes" if p.authorized_to_work is True
                else "no" if p.authorized_to_work is False
                else ""
            ),
            show_default=False,
        )
        parsed_authorized = _parse_optional_yes_no(authorized)
        if not authorized.strip() or parsed_authorized is not None:
            p.authorized_to_work = parsed_authorized
            break
        console.print("[yellow]Enter yes, no, or leave it blank.[/yellow]")
    while True:
        sponsorship = typer.prompt(
            "Will you now or later require employment sponsorship? (yes/no, blank if unknown)",
            default=(
                "yes" if p.requires_sponsorship is True
                else "no" if p.requires_sponsorship is False
                else ""
            ),
            show_default=False,
        )
        parsed_sponsorship = _parse_optional_yes_no(sponsorship)
        if not sponsorship.strip() or parsed_sponsorship is not None:
            p.requires_sponsorship = parsed_sponsorship
            break
        console.print("[yellow]Enter yes, no, or leave it blank.[/yellow]")
    while True:
        exp = typer.prompt(
            "Years of experience (leave blank if unknown)",
            default=(
                str(p.years_of_experience)
                if p.years_of_experience is not None
                else ""
            ),
            show_default=False,
        )
        if not exp.strip():
            p.years_of_experience = None
            break
        parsed_exp = _parse_years_of_experience(exp)
        if parsed_exp is not None and parsed_exp > 0:
            p.years_of_experience = parsed_exp
            break
        console.print(
            "[yellow]Enter a positive number like 5, or leave it blank if unknown.[/yellow]"
        )
    p.current_title = typer.prompt("Current job title", default=p.current_title or "")
    p.skills = _parse_csv_list(typer.prompt(
        "Skills (comma-separated)",
        default=", ".join(p.skills),
        show_default=False,
    ))
    p.target_titles = _parse_csv_list(typer.prompt(
        "Target role titles (comma-separated)",
        default=", ".join(p.target_titles),
        show_default=False,
    ))

    store.save(p)


@app.command()
def templates(
    add: bool = typer.Option(False, "--add", "-a", help="Add a new template"),
    question: Optional[str] = typer.Option(None, "--question", "-q", help="Question text"),
    answer: Optional[str] = typer.Option(None, "--answer", help="Answer text"),
):
    """Manage answer templates for common questions"""
    matcher = get_question_matcher()
    if add or (question and answer):
        if not question:
            question = typer.prompt("Question pattern")
        if not answer:
            answer = typer.prompt("Your answer")
        matcher.add_template(question, answer)
    else:
        matcher.display_templates()


@app.command()
def stats():
    """Show application statistics"""
    recorder = get_action_recorder()
    recorder.display_stats()
    tracker = get_application_tracker()
    tracker.display_recent(10)
    tracker.close()


@app.command()
def history(
    limit: int = typer.Option(20, help="Number of recent applications to show"),
):
    """Show recent application history"""
    tracker = get_application_tracker()
    s = tracker.get_stats()
    console.print(
        f"\n[bold]Application Summary:[/bold] {s['total']} tracked, "
        f"{s['applied']} applied, {s['submitted']} submitted, "
        f"{s['interview']} interview, {s['rejected']} rejected, "
        f"{s['in_progress']} in progress\n"
    )
    tracker.display_recent(limit)
    tracker.close()


@app.command()
def serve(
    host: str = typer.Option(
        "127.0.0.1",
        help="Bind address. Loopback by default. Remote binds accept only a "
        "specific Tailscale IPv4 address and require JOBPILOT_REMOTE_TOKEN.",
    ),
    port: Optional[int] = typer.Option(
        None,
        help="Port (default: 8767 — EYE uses 8766)",
    ),
):
    """Start the local dashboard, with optional authenticated Tailscale access.

    Binds to loopback (127.0.0.1) by default. To reach it from your phone over
    Tailscale, pass your machine's Tailscale IP explicitly with --host.
    """
    from jobpilot.core.config import DEFAULT_SERVE_PORT
    from jobpilot.core.server import configure_server_access, run_server

    serve_port = port or DEFAULT_SERVE_PORT
    try:
        remote = configure_server_access(host)
    except ValueError as exc:
        console.print(f"[red]Refusing unsafe server configuration: {exc}[/red]")
        raise typer.Exit(code=2) from exc

    console.print(Panel.fit(
        "[bold cyan]🚀 JobPilot Dashboard[/bold cyan]\n"
        + f"[bold]{'Tailscale' if remote else 'Local'}:[/bold]  http://{host}:{serve_port}/\n"
        + (
            "[yellow]Authentication required. Append "
            "?token=$JOBPILOT_REMOTE_TOKEN on first open.[/yellow]\n"
            if remote else "[dim]Loopback access does not require a token.[/dim]\n"
        )
        + "[dim]Keep this terminal open. Ctrl+C to stop.[/dim]",
        border_style="cyan",
        title="Dashboard Access",
    ))

    run_server(host=host, port=serve_port)


@app.command()
def hud(
    watch: bool = typer.Option(False, "--watch", "-w", help="Full-screen live HUD"),
    interval: float = typer.Option(30.0, "--interval", help="Refresh seconds (--watch)"),
    gigs: int = typer.Option(30, "--gigs", "-g", help="Max contract gigs to list"),
    jobs: int = typer.Option(20, "--jobs", "-j", help="Max backup ATS jobs to list"),
    pipeline: int = typer.Option(12, "--pipeline", "-p", help="Max pipeline rows"),
    min_gig_score: int = typer.Option(45, "--min-gig-score", help="Minimum gigs fit score"),
    contract_first: bool = typer.Option(True, "--contract-first/--all-gigs-types"),
    anti_schedule: bool = typer.Option(True, "--anti-schedule/--allow-schedule"),
    austin: bool = typer.Option(True, "--austin/--no-austin"),
    fresh_gigs: bool = typer.Option(True, "--fresh/--all-gigs", help="Only unseen gigs vs entire scan"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show numbered URL index for every row"),
    plain: bool = typer.Option(False, "--plain", help="Plain-language labels (command center / non-dev view)"),
    export_txt: bool = typer.Option(False, "--export", help="Plain-text dump to stdout (all rows + URLs)"),
    pick: bool = typer.Option(False, "--pick", help="fzf fuzzy-pick a gig or job and open its URL"),
):
    """Full-screen terminal HUD — gigs, jobs, pipeline, URLs, and next actions."""
    from jobpilot.ui.hud import export_hud_text, pick_hud, render_hud, watch_hud
    from jobpilot.ui.income_data import IncomeViewOptions

    opts = IncomeViewOptions(
        austin=austin,
        contract_first=contract_first,
        drop_rigid_schedule=anti_schedule,
        gigs_limit=gigs,
        jobs_limit=jobs,
        min_gig_score=min_gig_score,
        gigs_fresh_only=fresh_gigs,
        pipeline_limit=pipeline,
    )
    if export_txt:
        console.print(export_hud_text(opts=opts))
        return
    if pick:
        pick_hud(console, opts=opts)
        return
    if watch:
        watch_hud(console, opts=opts, interval=interval, verbose=verbose, plain=plain)
    else:
        render_hud(console, opts=opts, verbose=verbose, plain=plain)


@app.command()
def dashboard(
    save: Optional[Path] = typer.Option(None, "--save", help="Write the PNG here (in addition to showing it)"),
    ascii_only: bool = typer.Option(False, "--ascii", help="Force the plain-text version"),
    fetch: bool = typer.Option(False, "--fetch", help="Re-scrape sources for fresh data (slower)"),
):
    """One-glance visual dashboard — renders as a real image inline in iTerm,
    plain text everywhere else."""
    from jobpilot.ui.dashboard_image import (
        ascii_dashboard,
        collect_dashboard_data,
        render_dashboard_png,
    )
    from jobpilot.ui.inline_image import print_inline_image, supports_inline_images

    data = collect_dashboard_data(fetch=fetch)

    if ascii_only:
        console.print(ascii_dashboard(data))
        return

    png = render_dashboard_png(data)
    if save:
        Path(save).expanduser().write_bytes(png)
        console.print(f"[green]Saved dashboard → {save}[/green]")

    if supports_inline_images():
        print_inline_image(png, name="jobpilot-dashboard.png", width="auto")
    elif not save:
        console.print(ascii_dashboard(data))
        console.print("[dim]Tip: run this in iTerm2 (or pass --save) to see the graphical dashboard.[/dim]")


@app.command("center-status")
def center_status(
    watch: bool = typer.Option(False, "--watch", "-w", help="Live status dashboard"),
    interval: float = typer.Option(30.0, "--interval", help="Refresh seconds (--watch)"),
):
    """Plain-language status board for the iTerm command center left pane."""
    from jobpilot.ui.center_panes import render_status_board, watch_status_board

    if watch:
        watch_status_board(console, interval=interval)
    else:
        render_status_board(console)


@app.command("center-activity")
def center_activity(
    watch: bool = typer.Option(False, "--watch", "-w", help="Live activity feed"),
    interval: float = typer.Option(5.0, "--interval", help="Refresh seconds (--watch)"),
):
    """Human-readable activity feed for the iTerm command center bottom pane."""
    from jobpilot.ui.center_panes import render_activity_feed, watch_activity_feed

    if watch:
        watch_activity_feed(console, interval=interval)
    else:
        render_activity_feed(console)


@app.command()
def iterm(
    install: bool = typer.Option(False, "--install", help="Install iTerm profiles + shell hook (one-time)"),
    new_window: bool = typer.Option(False, "--new", help="Force a fresh command center (closes old JobPilot windows)"),
):
    """Open or focus the JobPilot iTerm command center (full-screen HUD)."""
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent
    iterm_dir = root / "scripts" / "iterm"

    if install:
        subprocess.run(["bash", str(iterm_dir / "install.sh")], check=True)
        return

    args = ["bash", str(iterm_dir / "launch-command-center.sh")]
    if new_window:
        args.append("--new")
    subprocess.run(args, check=True)


@app.command()
def radar(
    austin: bool = typer.Option(True, "--austin/--no-austin", help="Filter jobs to Austin + remote US"),
    contract_first: bool = typer.Option(True, "--contract-first/--all-gigs", help="Gigs: contract/1099/hourly first"),
    anti_schedule: bool = typer.Option(True, "--anti-schedule/--allow-schedule", help="Drop 9-5 / core-hours gigs"),
    gigs_top: int = typer.Option(8, "--gigs", help="Max contract gigs to show"),
    jobs_limit: int = typer.Option(8, "--jobs", help="Max backup ATS jobs to show"),
    min_gig_score: int = typer.Option(45, "--min-gig-score", help="Minimum gigs fit score"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Live refresh"),
    interval: float = typer.Option(30.0, "--interval", help="Watch refresh seconds"),
):
    """Autonomous income radar — contract gigs (primary) + ATS backup (secondary)."""
    from jobpilot.ui.income_data import IncomeViewOptions
    from jobpilot.ui.radar import render_radar, watch_radar

    opts = IncomeViewOptions(
        austin=austin,
        contract_first=contract_first,
        drop_rigid_schedule=anti_schedule,
        gigs_limit=gigs_top,
        jobs_limit=jobs_limit,
        min_gig_score=min_gig_score,
    )
    if watch:
        watch_radar(console, opts=opts, interval=interval)
    else:
        render_radar(console, opts=opts)


@app.command()
def board(
    fresh: bool = typer.Option(True, "--fresh/--all", help="Show only verified apply-ready roles (default) or full queue"),
    austin: bool = typer.Option(False, "--austin", "-a", help="Filter to Austin-area roles"),
    autonomous: bool = typer.Option(False, "--autonomous", help="Hide senior-titled queued roles"),
    location: str = typer.Option("", "--location", "-l", help="Substring filter on location field"),
    status: str = typer.Option("queued", "--status", "-s", help="Status filter: queued, applied, rejected, all, …"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows in the queue table"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Live-refresh the board every few seconds"),
    interval: float = typer.Option(5.0, "--interval", help="Refresh interval (seconds) for --watch"),
):
    """Visual terminal dashboard — queue, next-up, tracker, and service health."""
    from jobpilot.ui.terminal_board import BoardFilters, render_board, watch_board

    filters = BoardFilters(
        fresh=fresh,
        austin=austin,
        autonomous=autonomous,
        location=location.strip(),
        status=status.strip().lower() or "queued",
        limit=limit,
    )
    if watch:
        watch_board(console, filters=filters, interval=interval)
    else:
        render_board(console, filters=filters)


@app.command()
def gmail_sync(
    source: Path = typer.Argument(
        ...,
        help=(
            "Fresh full-history JSON export produced by a read-only Gmail source. "
            "The source must use JobPilot's generated_at + records cache schema."
        ),
    ),
):
    """Validate and atomically import application history without mutating Gmail."""
    from jobpilot.core.gmail_application_cache import (
        GmailCacheError,
        sync_gmail_application_cache,
    )
    from jobpilot.core.policy_config import get_policy

    evidence_policy = get_policy().application_evidence
    configured = evidence_policy.gmail_cache_path.strip()
    if not configured:
        console.print(
            "[red]Gmail cache sync is not configured.[/red]\n"
            "Set application_evidence.gmail_cache_path in data/policy.json, then retry."
        )
        raise typer.Exit(2)
    destination = resolve_data_path(configured)

    try:
        result = sync_gmail_application_cache(
            source,
            destination,
            max_age_hours=evidence_policy.gmail_cache_max_age_hours,
        )
    except GmailCacheError as exc:
        console.print(f"[red]Gmail cache was not changed:[/red] {exc}")
        raise typer.Exit(2) from exc

    console.print(
        f"[green]✓ Gmail application cache refreshed[/green] — "
        f"{result.record_count} records, generated {result.generated_at}\n"
        f"[dim]{result.destination}[/dim]\n"
        "Gmail was read by the external source only; JobPilot did not modify the mailbox."
    )


@app.command()
def queue(
    refresh: bool = typer.Option(False, "--refresh", "-r", help="Re-scan all portals and rebuild queue"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max jobs to queue"),
    open_dashboard: bool = typer.Option(True, "--open/--no-open", help="Show how to open the local dashboard server"),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help="Show only roles passing current posting, fit, history, and ledger gates.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit the queue (filtered) as JSON to stdout. Suppresses dashboard + console table. For piping into other tools/agents."),
    no_board: bool = typer.Option(False, "--no-board", help="Skip the terminal board after queue build/load"),
):
    """Scan ATS boards, assess roles, and show the evidence-led slate."""
    from jobpilot.core.application_evidence import configured_external_evidence_errors
    from jobpilot.core.policy_config import get_policy
    from jobpilot.core.queue_builder import (
        QUEUE_PATH,
        is_apply_ready,
        load_queue,
        reconcile_queue_with_tracker,
        refresh_queue,
        restrict_queue_for_action_provenance,
    )
    from jobpilot.ui.terminal_board import BoardFilters, render_board

    def configured_path(value: str) -> Path | None:
        if not value:
            return None
        return resolve_data_path(value)

    evidence_policy = get_policy().application_evidence
    history_errors = (
        configured_external_evidence_errors(
            employment_dir=configured_path(evidence_policy.employment_dir),
            gmail_cache_path=configured_path(evidence_policy.gmail_cache_path),
            gmail_cache_max_age_hours=evidence_policy.gmail_cache_max_age_hours,
        )
        if evidence_policy.fail_closed
        else ()
    )

    def show_history_warning(*, machine_output: bool) -> None:
        if not history_errors:
            return
        message = (
            "APPLICATION HISTORY INCOMPLETE: search can continue, but every "
            "otherwise-ready role is restricted to Investigate until the "
            "configured source is refreshed. " + "; ".join(history_errors)
        )
        if machine_output:
            typer.echo(message, err=True)
        else:
            console.print(
                Panel.fit(
                    message,
                    title="Application safety gate",
                    border_style="yellow",
                )
            )

    def refresh_with_clear_evidence_error():
        from jobpilot.core.application_evidence import EvidenceSourceError

        try:
            return refresh_queue(limit=limit)
        except EvidenceSourceError as exc:
            typer.echo("Queue refresh stopped: application history is incomplete.", err=True)
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc

    # JSON mode: emit raw queue (respecting --fresh and --limit) and exit.
    # Designed for cross-agent / scripting use without dashboard/UI side effects.
    if as_json:
        from jobpilot.core.agent_contract import AgentServiceError
        from jobpilot.core.jobpilot_service import JobPilotService

        service = JobPilotService(review_resolver=_claim_state_for_job)
        if refresh or not QUEUE_PATH.exists():
            try:
                result = service.refresh_opportunities(
                    ready_only=fresh,
                    limit=limit,
                )
            except AgentServiceError as exc:
                typer.echo(exc.message, err=True)
                typer.echo(exc.action, err=True)
                raise typer.Exit(2) from exc
        else:
            reconcile_queue_with_tracker()
            result = service.list_opportunities(
                ready_only=fresh,
                limit=limit,
            )
        show_history_warning(machine_output=True)
        console.print_json(data=result.data["items"])
        return

    if not refresh and QUEUE_PATH.exists():
        reconcile_queue_with_tracker()
        jobs = load_queue()
        restrict_queue_for_action_provenance(jobs)
        ready = [j for j in jobs if is_apply_ready(j)]
        console.print(
            f"[cyan]Loaded existing queue: {len(ready)} verified roles ready to apply[/cyan]"
        )
        if not ready:
            console.print("[dim]Run with --refresh to re-scan portals[/dim]")
    else:
        console.print("[cyan]Scanning ATS boards (this takes ~30 seconds)...[/cyan]")
        jobs = refresh_with_clear_evidence_error()
        if not jobs:
            console.print("[yellow]No jobs found. Check data/portals.json and your internet connection.[/yellow]")
            raise typer.Exit(1)
        console.print(f"[green]✓ Built queue: {len(jobs)} jobs across tech + field ops[/green]")

    show_history_warning(machine_output=False)

    if not no_board:
        render_board(
            console,
            filters=BoardFilters(
                fresh=fresh,
                limit=min(limit, 20),
            ),
        )

    if open_dashboard:
        console.print(
            "[cyan]Queue saved.[/cyan] Run [bold]jobpilot serve[/bold], then open "
            "[link=http://127.0.0.1:8767/]http://127.0.0.1:8767/[/link]. "
            "The dashboard requires its local API server."
        )


@app.command()
def apply(
    job_id: str = typer.Argument(..., help="Job ID from the queue (shown in dashboard or 'jobpilot queue')"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the preparation gate without changing anything"),
    claim_approved: bool = typer.Option(False, "--claim-approved", help="Use only if you've already approved staging this reviewed target"),
):
    """Prepare a verified role for the human paste-and-submit flow."""
    from jobpilot.core.queue_builder import (
        get_job,
        has_current_action_provenance,
        is_apply_ready,
    )

    job = get_job(job_id)
    if not job:
        console.print(f"[red]Job '{job_id}' not found in queue. Run 'jobpilot queue' first.[/red]")
        raise typer.Exit(1)

    if job.status in {"applied", "submitted"}:
        console.print(f"[yellow]Already applied to {job.title} @ {job.company}[/yellow]")
        if not typer.confirm("Apply again anyway?"):
            raise typer.Exit(0)

    if not is_apply_ready(job) or not has_current_action_provenance(job):
        console.print(
            "[yellow]This role is not apply-ready.[/yellow]\n"
            f"Decision: {getattr(job, 'decision', 'investigate')} · "
            f"Posting evidence: {getattr(job, 'legitimacy_state', 'hold')} "
            f"({getattr(job, 'evidence_grade', 'F')})\n"
            "Next action: refresh the source and review the evidence card."
        )
        raise typer.Exit(1)

    console.print(Panel.fit(
        f"[bold cyan]{job.title}[/bold cyan]\n"
        f"[white]{job.company}[/white]  •  {job.location}\n"
        f"[dim]{job.url}[/dim]\n"
        f"[bold]Qualification floor: "
        f"{job.qualification_lower_bound if job.qualification_lower_bound is not None else 'unknown'}"
        f"[/bold]  •  Evidence coverage: {job.evidence_coverage}%",
        border_style="cyan",
        title="Preparing verified role",
    ))

    if dry_run:
        console.print("[dim]Dry run — no files or browser state changed.[/dim]")
        raise typer.Exit(0)

    _enforce_claim_lock(job, claim_approved=claim_approved)
    console.print(
        "[green]✓ Role cleared for preparation.[/green]\n"
        f"Open the posting in your normal browser: [link={job.url}]{job.url}[/link]\n"
        "Draft answers and a tailored résumé from verified evidence, create a paste sheet, "
        "then paste, review, and submit by hand. JobPilot will not fill the live ATS form."
    )


@app.command()
def report(
    csv_export: bool = typer.Option(False, "--csv", help="Export to CSV file"),
    days: int = typer.Option(7, "--days", "-d", help="Number of days to include"),
):
    """Generate application analytics report"""
    from jobpilot.core.analytics import daily_digest, export_csv

    if csv_export:
        path = export_csv(days=days)
        console.print(f"[green]✓ Exported to {path}[/green]")
    else:
        daily_digest(days=days)


@app.command()
def review(
    threshold: float = typer.Option(0.8, "--threshold", "-t", help="Show templates with approval rate below this"),
    fix: bool = typer.Option(False, "--fix", "-f", help="Interactive mode: edit or delete each template"),
):
    """Review templates with low approval rates"""
    from rich.prompt import Confirm, Prompt
    from rich.table import Table

    from jobpilot.learning.learning_db import get_learning_db

    db = get_learning_db()
    items = db.templates_with_stats()

    # Filter to templates that have actions and are below threshold
    flagged = [
        t for t in items
        if t["approval_rate"] is not None and t["approval_rate"] < threshold
    ]

    if not flagged:
        console.print(f"[green]✓ All templates are above {threshold:.0%} approval rate.[/green]")
        # Still show a summary table
        if items:
            console.print(f"[dim]{len(items)} templates total, all healthy.[/dim]")
        return

    console.print(f"\n[bold yellow]⚠  {len(flagged)} template(s) below {threshold:.0%} approval rate[/bold yellow]\n")

    if not fix:
        # Display-only mode
        table = Table(title="Templates Needing Review")
        table.add_column("Question", style="cyan", max_width=45)
        table.add_column("Answer", style="white", max_width=30)
        table.add_column("Rate", justify="right", width=8)
        table.add_column("Used", justify="right", width=6)

        for t in flagged:
            rate = f"{t['approval_rate']:.0%}" if t["approval_rate"] is not None else "—"
            q = t["question"][:42] + "..." if len(t["question"]) > 45 else t["question"]
            a = t["answer"][:27] + "..." if len(t["answer"]) > 30 else t["answer"]
            table.add_row(q, a, rate, str(t["total_actions"]))

        console.print(table)
        console.print("\n[dim]Run with --fix to interactively edit or delete these.[/dim]")
        return

    # Interactive fix mode
    for i, t in enumerate(flagged, 1):
        rate = f"{t['approval_rate']:.0%}" if t["approval_rate"] is not None else "—"
        console.print(f"\n[bold]({i}/{len(flagged)})[/bold]  Rate: [yellow]{rate}[/yellow]  Used: {t['total_actions']}x")
        console.print(f"  [cyan]Q:[/cyan] {t['question']}")
        console.print(f"  [white]A:[/white] {t['answer']}")

        action = Prompt.ask(
            "  Action",
            choices=["keep", "edit", "delete", "skip"],
            default="keep",
        )

        if action == "edit":
            new_answer = Prompt.ask("  New answer", default=t["answer"])
            db.upsert_template(t["question"], new_answer)
            console.print("  [green]✓ Updated[/green]")
        elif action == "delete":
            if Confirm.ask("  Really delete?", default=False):
                db.delete_template(t["question"])
                console.print("  [red]✗ Deleted[/red]")
        elif action == "skip":
            break

    console.print("\n[green]✓ Review complete[/green]")


answer_app = typer.Typer(help="Manage paste-ready application answers (save / copy / list / show).")
app.add_typer(answer_app, name="answer")

# Gigs lane (former GigPilot). Not lazy: Typer's add_typer needs the actual
# Typer instance at registration time, so the sub-app module must be imported
# here. Its import cost is small (no network; state paths resolve from env).
from jobpilot.gigs.cli import app as gigs_app  # noqa: E402

app.add_typer(
    gigs_app,
    name="gigs",
    help="Freelance-gig radar: scan sources, score, digest, push",
)


def _answers_dir() -> Path:
    """Root for stored answers: projects/jobpilot/data/answers/."""
    from jobpilot.core.config import DATA_DIR
    p = DATA_DIR / "answers"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _slug(s: str) -> str:
    """Lowercase dash slug for answer filenames and fuzzy JD lookup."""
    import re as _re
    return _re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "untitled"


def _answer_path(company: str, question: str) -> Path:
    """`data/answers/<company-slug>/<question-slug>.txt`. Slugs are lowercase, dash-separated."""
    return _answers_dir() / _slug(company) / f"{_slug(question)}.txt"


def _verify_ascii(path: Path) -> Optional[str]:
    """Return None if file is pure ASCII; else a sample of offending chars."""
    try:
        raw = path.read_bytes()
    except Exception as exc:
        return f"unreadable ({exc})"
    bad = sorted({b for b in raw if b > 127})
    if not bad:
        return None
    return f"non-ASCII bytes: {bad[:10]}"


def _copy_file_to_clipboard(path: Path) -> bool:
    import shutil
    import subprocess as _sp

    if not shutil.which("pbcopy"):
        return False
    _sp.run(["pbcopy"], input=path.read_bytes(), check=True)
    return True


def _resolve_answer_jd(company: str, jd: Optional[str]) -> tuple[str, str]:
    """Resolve an explicit JD source, else best matching file in data/jds."""
    if jd:
        return _load_score_source(jd)

    from jobpilot.core.config import DATA_DIR

    jds_dir = DATA_DIR / "jds"
    if not jds_dir.exists():
        return "", "no JD provided"

    company_slug = _slug(company)
    candidates = [
        path for path in jds_dir.glob("*.txt")
        if company_slug in _slug(path.stem)
    ]
    if not candidates:
        return "", "no JD provided"

    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    selected = candidates[0]
    return selected.read_text(), str(selected)


def _latest_draft_title_for_company(company: str) -> str:
    from jobpilot.core.config import DATA_DIR

    manifest = DATA_DIR / "resumes" / "latest_draft.json"
    if not manifest.exists():
        return ""
    try:
        data = json.loads(manifest.read_text())
    except Exception:
        return ""
    if _slug(str(data.get("company", ""))) != _slug(company):
        return ""
    return str(data.get("title", "")).strip()


@answer_app.command("save")
def answer_save(
    company: str = typer.Argument(..., help="Company slug (e.g. 'extend')"),
    question: str = typer.Argument(..., help="Question slug (e.g. 'q1-vetnav')"),
    from_file: Optional[Path] = typer.Option(None, "--from-file", "-f", help="Source text file (overrides --text)"),
    text: Optional[str] = typer.Option(None, "--text", "-t", help="Inline answer text"),
    pbcopy: bool = typer.Option(True, "--pbcopy/--no-pbcopy", help="Also load to clipboard after saving"),
):
    """Save a paste-ready answer to `data/answers/<company>/<question>.txt`.

    Auto-verifies pure ASCII so the paste won't carry em-dashes / curly quotes
    that ATS forms render as AI-usage tells. Optionally pbcopies in one step.
    """
    import shutil

    if from_file is None and not text:
        console.print("[red]Provide --from-file <path> or --text '...'.[/red]")
        raise typer.Exit(1)

    target = _answer_path(company, question)
    target.parent.mkdir(parents=True, exist_ok=True)

    if from_file is not None:
        if not from_file.exists():
            console.print(f"[red]Source file not found: {from_file}[/red]")
            raise typer.Exit(1)
        shutil.copyfile(from_file, target)
    else:
        target.write_text(text or "")

    issue = _verify_ascii(target)
    if issue:
        console.print(f"[yellow]⚠ {target.name} contains {issue} — paste-quality risk; clean and re-save.[/yellow]")
    chars = len(target.read_text())
    words = len(target.read_text().split())
    console.print(f"[green]✓ Saved[/green] {target.relative_to(_answers_dir().parent.parent)} · {chars} chars · {words} words")

    if pbcopy:
        if _copy_file_to_clipboard(target):
            console.print("[cyan]→ on clipboard. Cmd+V in the field, then Enter where you want paragraph breaks.[/cyan]")
        else:
            console.print("[yellow]pbcopy not found — clipboard skipped (non-macOS?).[/yellow]")


@answer_app.command("draft")
def answer_draft(
    company: str = typer.Argument(..., help="Company slug/name, e.g. titan-ai"),
    question: str = typer.Argument(..., help="Exact application question text"),
    jd: Optional[str] = typer.Option(None, "--jd", help="JD text or path. If omitted, JobPilot uses latest data/jds match for company."),
    title: str = typer.Option("", "--title", help="Target role title. Defaults to latest draft title when company matches."),
    question_slug: Optional[str] = typer.Option(None, "--question-slug", help="Filename slug to save under data/answers/<company>/"),
    max_words: int = typer.Option(160, "--max-words", min=40, max=350, help="Target word cap for narrative answers."),
    save: bool = typer.Option(True, "--save/--no-save", help="Save to data/answers after drafting."),
    pbcopy: bool = typer.Option(True, "--pbcopy/--no-pbcopy", help="Copy saved/drafted answer to clipboard."),
    bro: bool = typer.Option(True, "--bro/--no-bro", help="Use the AI backend (local Bro or Gemini) when available; fallback stays account-grounded."),
):
    """Draft a role-tailored answer from true accounts and save/copy it."""
    jd_text, jd_label = _resolve_answer_jd(company, jd)
    role_title = title.strip() or _latest_draft_title_for_company(company)

    answerer = ApplicationAnswerer(use_bro=bro)
    draft = answerer.draft(
        question,
        jd_text=jd_text,
        company=company,
        title=role_title,
        max_words=max_words,
    )

    if not draft.answer:
        console.print("[red]No answer drafted.[/red]")
        for warning in draft.warnings:
            console.print(f"[yellow]{warning}[/yellow]")
        console.print(f"[dim]Add true accounts in {TRUE_ACCOUNTS_PATH}.[/dim]")
        raise typer.Exit(1)

    console.print(Panel.fit(
        f"[bold cyan]{company}[/bold cyan]\n"
        f"[white]{role_title or 'Role title not set'}[/white]\n"
        f"[dim]Question:[/dim] {draft.question}\n"
        f"[dim]JD:[/dim] {jd_label}\n"
        f"[dim]Source:[/dim] {draft.source} | accounts: {', '.join(draft.account_ids)}\n\n"
        f"{draft.answer}",
        title="Application Answer Draft",
        border_style="cyan",
    ))

    for warning in draft.warnings:
        console.print(f"[yellow]{warning}[/yellow]")

    target: Optional[Path] = None
    if save:
        target = _answer_path(company, question_slug or question[:72])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(draft.answer)
        issue = _verify_ascii(target)
        if issue:
            console.print(f"[yellow]Paste-quality warning: {issue}[/yellow]")
        console.print(f"[green]Saved[/green] {target.relative_to(_answers_dir().parent.parent)}")

    if pbcopy:
        if target:
            copied = _copy_file_to_clipboard(target)
        else:
            import shutil
            import subprocess as _sp
            copied = bool(shutil.which("pbcopy"))
            if copied:
                _sp.run(["pbcopy"], input=draft.answer.encode("utf-8"), check=True)
        if copied:
            console.print("[cyan]Answer is on clipboard.[/cyan]")
        else:
            console.print("[yellow]pbcopy not found - clipboard skipped.[/yellow]")


@answer_app.command("accounts")
def answer_accounts():
    """List true accounts available to the answer generator."""
    from rich.table import Table

    accounts = ApplicationAnswerer(use_bro=False).load_accounts()
    if not accounts:
        console.print(f"[yellow]No true accounts found at {TRUE_ACCOUNTS_PATH}[/yellow]")
        return

    table = Table(title="True Accounts", show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Account", style="white")
    table.add_column("Skills", style="dim")
    for account in accounts:
        table.add_row(account.id, account.title, ", ".join(account.skills[:5]))
    console.print(table)


@answer_app.command("copy")
def answer_copy(
    company: str = typer.Argument(..., help="Company slug"),
    question: str = typer.Argument(..., help="Question slug"),
):
    """Load a saved answer onto your clipboard. `jobpilot answer copy extend q1-vetnav`."""
    import shutil

    target = _answer_path(company, question)
    if not target.exists():
        # Try fuzzy: list anything matching either token
        hits = list(_answers_dir().rglob("*.txt"))
        suggestions = [str(h.relative_to(_answers_dir())) for h in hits
                       if company.lower() in str(h).lower() or question.lower() in str(h).lower()]
        console.print(f"[red]Not found:[/red] {target}")
        if suggestions:
            console.print("[dim]Did you mean one of:[/dim]")
            for s in suggestions[:8]:
                console.print(f"  [dim]{s}[/dim]")
        raise typer.Exit(1)

    if not shutil.which("pbcopy"):
        console.print("[yellow]pbcopy not found — printing to stdout instead.[/yellow]")
        console.print(target.read_text())
        return

    _copy_file_to_clipboard(target)
    chars = len(target.read_text())
    console.print(f"[cyan]✓ on clipboard[/cyan] · {target.name} · {chars} chars")
    console.print("[dim]Cmd+V in the field, then Enter where you want paragraph breaks.[/dim]")


@answer_app.command("list")
def answer_list(
    company: Optional[str] = typer.Argument(None, help="Optional: filter to one company"),
):
    """List saved answers, grouped by company."""
    from rich.table import Table
    root = _answers_dir()
    rows: list[tuple[str, str, int, str]] = []
    for path in sorted(root.rglob("*.txt")):
        comp = path.parent.name
        if company and company.lower() != comp.lower():
            continue
        chars = len(path.read_text())
        ts = path.stat().st_mtime
        from datetime import datetime as _dt
        rows.append((comp, path.stem, chars, _dt.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")))

    if not rows:
        console.print("[yellow]No saved answers yet.[/yellow]")
        return

    table = Table(title="Saved answers", show_header=True, header_style="bold")
    table.add_column("Company", style="cyan")
    table.add_column("Question", style="green")
    table.add_column("Chars", style="dim", justify="right")
    table.add_column("Updated", style="dim")
    for r in rows:
        table.add_row(r[0], r[1], str(r[2]), r[3])
    console.print(table)
    console.print("[dim]Copy any of these with: jobpilot answer copy <company> <question>[/dim]")


@answer_app.command("show")
def answer_show(
    company: str = typer.Argument(...),
    question: str = typer.Argument(...),
):
    """Print a saved answer to stdout (for piping or inspection)."""
    target = _answer_path(company, question)
    if not target.exists():
        console.print(f"[red]Not found: {target}[/red]")
        raise typer.Exit(1)
    console.print(target.read_text())


@app.command()
def log(
    company: str = typer.Argument(..., help="Company name (used for dedup)"),
    title: str = typer.Option("", "--title", "-t", help="Role title"),
    url: str = typer.Option("", "--url", "-u", help="Apply URL (used as primary dedup key)"),
    status: str = typer.Option(
        "applied",
        "--status",
        "-s",
        help=(
            "applied | outreach | human_reply | screen | interview | offer | "
            "rejected | withdrawn | no_response"
        ),
    ),
    date: Optional[str] = typer.Option(None, "--date", "-d", help="ISO date (defaults to today)"),
    channel: str = typer.Option(
        "unknown",
        "--channel",
        help="How the role was acquired: direct | referral | recruiter | outreach | unknown",
    ),
):
    """Log an application to the tracker.

    Closes the gap where manual/external applies (Ashby, Lever direct, etc.)
    don't auto-write to applications.db, so subsequent `queue --fresh` runs
    correctly dedup them. Idempotent on URL.
    """
    from jobpilot.core.agent_contract import AgentServiceError
    from jobpilot.core.jobpilot_service import JobPilotService
    from jobpilot.core.outcome_service import OutcomeInput

    try:
        result = JobPilotService().record_outcome(
            OutcomeInput(
                company=company,
                title=title,
                url=url,
                status=status,
                occurred_at=date,
                human_confirmed=True,
                acquisition_channel=channel,
                source="manual-log",
            )
        )
    except AgentServiceError as exc:
        console.print(f"[red]{exc.message}[/red]\n[yellow]{exc.action}[/yellow]")
        raise typer.Exit(1) from exc
    application = result.data["application"]
    console.print(
        f"[green]✓ Tracked: {application['company']} — "
        f"{application['job_title'] or '(role)'} [{application['status']}][/green]"
    )
    console.print(f"[dim]tracker now: {result.data['tracker_total']} rows[/dim]")
    get_application_tracker().close()


@app.command()
def reconcile():
    """Reconcile queue.json statuses with applications.db."""
    from jobpilot.core.queue_builder import reconcile_queue_with_tracker

    changed, total = reconcile_queue_with_tracker()
    console.print(f"[green]✓ Reconciled queue[/green] — {changed} changed / {total} jobs")


@app.command()
def focus():
    """Collapse active queue to one best role per company."""
    from jobpilot.core.queue_builder import focus_queue_company_first

    changed, total, companies = focus_queue_company_first()
    console.print(
        f"[green]✓ Focused active queue[/green] — skipped {changed} duplicate roles "
        f"across {companies} companies / {total} jobs"
    )


if __name__ == "__main__":
    app()
