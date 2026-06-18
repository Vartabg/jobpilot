"""
JobPilot CLI — thin entry point.

Usage:
    jobpilot start       - Launch and connect to Chrome
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

from jobpilot.core.cdp_bridge import connect_to_chrome
from jobpilot.core.profile_store import get_profile_store
from jobpilot.core.question_matcher import get_question_matcher
from jobpilot.core.application_tracker import get_application_tracker
from jobpilot.core.autonomy import AutonomyMode, get_autonomy_config, set_autonomy_mode
from jobpilot.core.logger import get_logger
from jobpilot.core.events import EventBus, INFO, WARNING, ERROR, FIELD_FILLED, FIELD_SKIPPED, FIELD_EDITED, APPLICATION_STARTED, APPLICATION_SUBMITTED, APPLICATION_ABANDONED
from jobpilot.core.engine import ApplicationEngine
from jobpilot.core import llm_client
from jobpilot.core.bro_client import get_health
from jobpilot.core.jd_parser import JDParser
from jobpilot.core.job_scorer import JobScorer
from jobpilot.core.portal_scanner import PortalScanner, ScanTarget
from jobpilot.core.doctor import run_doctor
from jobpilot.core.resume_tailor import ResumeTailor
from jobpilot.core.application_answerer import ApplicationAnswerer, TRUE_ACCOUNTS_PATH
from jobpilot.learning.action_recorder import get_action_recorder

# Named `logger`, not `log`: the `log` CLI command below would shadow it and
# any logger call would crash with AttributeError.
logger = get_logger(__name__)

app = typer.Typer(
    name="jobpilot",
    help="Semi-automated LinkedIn job application copilot",
)
console = Console()

CLAUDE_VETTED_TARGETS_DIR = Path(__file__).parent / "data" / "reports"
CLAUDE_VETTED_TARGETS_GLOB = "claude-vetted-targets-*.json"
# Explicit override (tests patch this). When None, the newest matching report
# in CLAUDE_VETTED_TARGETS_DIR is used. When no file exists at all, the
# claim-lock gate is considered not configured and is skipped.
CLAUDE_VETTED_TARGETS_PATH: Optional[Path] = None


def _resolve_claim_lock_path() -> Optional[Path]:
    """Return the newest claude-vetted-targets report, or None if not configured."""
    if CLAUDE_VETTED_TARGETS_PATH is not None:
        path = Path(CLAUDE_VETTED_TARGETS_PATH)
        return path if path.exists() else None
    candidates = [
        path
        for path in CLAUDE_VETTED_TARGETS_DIR.glob(CLAUDE_VETTED_TARGETS_GLOB)
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


# ---------------------------------------------------------------------------
# Event → Rich Console wiring
# ---------------------------------------------------------------------------

def _wire_events(bus: EventBus) -> None:
    """Subscribe console output to engine events."""

    def _on_info(message: str = "", **_kw):
        console.print(f"[cyan]{message}[/cyan]")

    def _on_warning(message: str = "", **_kw):
        console.print(f"[yellow]{message}[/yellow]")

    def _on_error(message: str = "", **_kw):
        console.print(f"[red]{message}[/red]")

    def _on_field_filled(label: str = "", **_kw):
        console.print(f"[green]⚡ Auto-filled {label}[/green]")

    def _on_field_skipped(label: str = "", **_kw):
        console.print(f"[dim]✗ Skipped {label}[/dim]")

    def _on_field_edited(label: str = "", old: str = "", new: str = "", **_kw):
        console.print(f"[yellow]🧠 Learned correction: {label}[/yellow]")

    def _on_app_started(title: str = "", **_kw):
        console.print(f"\n[bold cyan]📋 Application Started: {title[:60]}[/bold cyan]")

    def _on_app_submitted(**_kw):
        console.print("\n[bold green]🎊 SUCCESS: Application Submitted! 🎉[/bold green]\n")

    def _on_app_abandoned(step: int = 1, **_kw):
        console.print(f"[dim]⚠  Application abandoned at step {step}[/dim]")

    bus.on(INFO, _on_info)
    bus.on(WARNING, _on_warning)
    bus.on(ERROR, _on_error)
    bus.on(FIELD_FILLED, _on_field_filled)
    bus.on(FIELD_SKIPPED, _on_field_skipped)
    bus.on(FIELD_EDITED, _on_field_edited)
    bus.on(APPLICATION_STARTED, _on_app_started)
    bus.on(APPLICATION_SUBMITTED, _on_app_submitted)
    bus.on(APPLICATION_ABANDONED, _on_app_abandoned)


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

        from pathlib import Path
        import sys

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
    if candidate.exists() and candidate.is_file():
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
    return source, "inline text"


def _parse_years_of_experience(raw: str) -> Optional[int]:
    """Parse a years-of-experience answer ('5', '5 years', '12+ yrs') into an int.

    Returns None when no leading number can be found.
    """
    match = re.match(r"(\d+)", str(raw or "").strip())
    return int(match.group(1)) if match else None


def _norm_claim_text(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _load_claim_targets(lock_path: Path) -> list[dict]:
    try:
        data = json.loads(lock_path.read_text())
    except Exception as exc:
        raise RuntimeError(f"Could not read claim-lock file: {exc}") from exc
    targets = data.get("targets", [])
    return targets if isinstance(targets, list) else []


def _find_claim_target(job, lock_path: Path) -> Optional[dict]:
    job_company = _norm_claim_text(getattr(job, "company", ""))
    job_title = _norm_claim_text(getattr(job, "title", ""))
    job_url = _norm_claim_text(getattr(job, "url", ""))

    company_matches: list[dict] = []
    for target in _load_claim_targets(lock_path):
        target_url = _norm_claim_text(target.get("url"))
        if target_url and target_url == job_url:
            return target
        if _norm_claim_text(target.get("company")) == job_company:
            company_matches.append(target)

    if len(company_matches) == 1:
        return company_matches[0]

    for target in company_matches:
        target_title = _norm_claim_text(target.get("title"))
        if target_title and (target_title == job_title or target_title in job_title or job_title in target_title):
            return target
    return None


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
        console.print("[dim]Claude must vet it and set materials_status to 'ready' before JobPilot fills it.[/dim]")
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
            f"Claude materials are ready for {job.company}. Approve staging this target now?",
            default=False,
        )
        if not approved:
            console.print("[dim]Not staged. Waiting for your explicit approval.[/dim]")
            raise typer.Exit(0)


def _render_score_result(result, source_label: str) -> None:
    """Display a job-fit score in the terminal."""
    from rich.table import Table

    border_style = "green" if result.score >= 65 else "yellow" if result.score >= 50 else "red"
    title = result.parsed_jd.title or "Job Fit Review"
    company = result.parsed_jd.company or "Unknown company"

    console.print(Panel.fit(
        f"[bold cyan]{title}[/bold cyan]\n"
        f"[white]{company}[/white]\n"
        f"[bold]{result.score}/100[/bold] • {result.recommendation}\n"
        f"[dim]{source_label}[/dim]",
        border_style=border_style,
    ))

    table = Table(title="Fit Breakdown")
    table.add_column("Area", style="cyan")
    table.add_column("Points", justify="right", style="white")
    for label, points in result.components.items():
        table.add_row(label, str(points))
    console.print(table)

    if result.matched_skills:
        console.print(f"[green]Matched:[/green] {', '.join(result.matched_skills[:6])}")
    if result.missing_skills:
        console.print(f"[yellow]Gaps:[/yellow] {', '.join(result.missing_skills[:6])}")

    for strength in result.strengths[:3]:
        console.print(f"[green]•[/green] {strength}")
    for risk in result.risks[:3]:
        console.print(f"[yellow]•[/yellow] {risk}")

    if result.ai_summary:
        console.print(f"\n[dim]{result.ai_summary}[/dim]")


def _render_resume_result(result, source_label: str) -> None:
    """Display the output of a tailored resume draft."""
    parsed = result.fit_result.parsed_jd
    saved_paths = [f"Markdown: {result.output_path}"]
    if result.html_path:
        saved_paths.append(f"HTML: {result.html_path}")
    if result.pdf_path:
        saved_paths.append(f"PDF: {result.pdf_path}")

    console.print(Panel.fit(
        f"[bold cyan]ATS Resume Draft Ready[/bold cyan]\n"
        f"[white]{parsed.title or 'Target role'} @ {parsed.company or 'Target company'}[/white]\n"
        f"[bold]{result.fit_result.score}/100[/bold] • {result.fit_result.recommendation}\n"
        f"[dim]{source_label}\n" + "\n".join(saved_paths) + "[/dim]",
        border_style="cyan",
    ))

    if result.keywords:
        console.print(f"[green]Keywords:[/green] {', '.join(result.keywords[:10])}")
    for line in result.summary_lines[:3]:
        console.print(f"[green]•[/green] {line}")


def _render_scan_results(jobs, *, limit: int = 20) -> None:
    """Display scanned ATS matches in a terminal table."""
    from rich.table import Table

    if not jobs:
        console.print("[yellow]No matching jobs found.[/yellow]")
        return

    table = Table(title=f"Portal Matches ({min(len(jobs), limit)} shown / {len(jobs)} total)")
    table.add_column("Portal", style="cyan", width=10)
    table.add_column("Company", style="white", max_width=20)
    table.add_column("Role", style="green", max_width=34)
    table.add_column("Location", style="magenta", max_width=18)
    table.add_column("Match", style="yellow", max_width=18)

    for job in jobs[:limit]:
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
    """Score the currently open LinkedIn job page in Chrome."""
    console.print("\n[cyan]Connecting to Chrome for active job scoring...[/cyan]")
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

    result = JobScorer().score_parsed_jd(parsed_jd)
    _render_score_result(result, page_info.url)
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
    port: int = typer.Option(9222, help="Chrome debugging port"),
    watch: bool = typer.Option(True, help="Stay connected and watch for applications"),
    mode: str = typer.Option("semi-auto", help="Autonomy: suggest, semi-auto, full-auto"),
):
    """Connect to Chrome and start assisting with job applications"""
    try:
        set_autonomy_mode(AutonomyMode(mode))
    except ValueError:
        console.print(f"[red]Invalid mode '{mode}'. Use: suggest, semi-auto, full-auto[/red]")
        raise typer.Exit(1)

    console.print(Panel.fit(
        f"[bold cyan]🚀 JobPilot Smart Dashboard[/bold cyan]\n"
        f"[dim]Mode: {mode} | Strategy: LinkedIn Fast-Track[/dim]\n"
        f"[dim]Status: [green]Active & Monitoring[/green][/dim]",
        border_style="cyan",
    ))

    try:
        asyncio.run(_start_async(port, watch))
    except KeyboardInterrupt:
        # Ctrl+C lands here, not inside the coroutine — asyncio cancels the
        # task and re-raises KeyboardInterrupt out of asyncio.run().
        console.print("\n[cyan]Session saved. Run 'jobpilot start' to resume.[/cyan]")


async def _start_async(port: int, watch: bool):
    """Bootstrap services, wire events, delegate to engine."""
    console.print("\n[cyan]Launching browser...[/cyan]")
    bridge = await connect_to_chrome(port)

    if not bridge:
        console.print("\n[red]Failed to launch browser. Check logs above.[/red]")
        return

    # Auto-index resume
    _check_and_index_resume()

    # Wire event bus
    events = EventBus()
    _wire_events(events)

    # Assemble engine
    engine = ApplicationEngine(
        bridge=bridge,
        events=events,
        overlay=None,
        chat_overlay=None,
        profile_store=get_profile_store(),
        question_matcher=get_question_matcher(),
        action_recorder=get_action_recorder(),
        app_tracker=get_application_tracker(),
        autonomy_config=get_autonomy_config(),
    )

    await engine.run(watch=watch)


@app.command()
def scan(
    greenhouse: Optional[list[str]] = typer.Option(None, "--greenhouse", help="Greenhouse board token; repeat for multiple."),
    lever: Optional[list[str]] = typer.Option(None, "--lever", help="Lever site token; repeat for multiple."),
    ashby: Optional[list[str]] = typer.Option(None, "--ashby", help="Ashby org slug; repeat for multiple."),
    keyword: Optional[list[str]] = typer.Option(None, "--keyword", "-k", help="Keyword filter for titles or locations."),
    config: Optional[Path] = typer.Option(None, "--config", help="Optional JSON file of scan targets."),
    limit: int = typer.Option(20, help="Maximum number of rows to show"),
    save: bool = typer.Option(True, "--save/--no-save", help="Persist scan results to `data/reports/`"),
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
    jobs.sort(key=lambda item: (item.company.lower(), item.title.lower()))
    _render_scan_results(jobs, limit=limit)

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
    bro: bool = typer.Option(True, "--bro/--no-bro", help="Use the AI backend (local Bro or Gemini) for more personalized tailoring when available"),
):
    """Generate an ATS-friendly resume draft tailored to a target role."""
    if active or not source:
        if not asyncio.run(
            _resume_active_page(
                port,
                output=output,
                use_bro=bro,
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
    tailor = ResumeTailor(use_bro=bro)
    result = tailor.generate_from_text(
        text,
        output_path=output,
        export_html=html or pdf,
        export_pdf=pdf,
    )
    _render_resume_result(result, label)


@app.command()
def score(
    source: Optional[str] = typer.Argument(
        None,
        help="Job description text or a path to a JD text file. Omit to score the active LinkedIn page.",
    ),
    port: int = typer.Option(9222, help="Chrome debugging port for --active mode"),
    active: bool = typer.Option(False, "--active", help="Score the active LinkedIn job tab in Chrome"),
):
    """Score a role before spending time on the application."""
    if active or not source:
        if not asyncio.run(_score_active_page(port)):
            raise typer.Exit(1)
        return

    if source.startswith(("http://", "https://")):
        console.print("[yellow]Open the job in Chrome and run `jobpilot score --active`, or paste the JD text directly.[/yellow]")
        raise typer.Exit(1)

    text, label = _load_score_source(source)
    result = JobScorer().score_text(text)
    _render_score_result(result, label)


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
    p.linkedin_url = typer.prompt("LinkedIn URL", default=p.linkedin_url or "")
    p.portfolio_url = typer.prompt("Portfolio URL", default=p.portfolio_url or "")
    p.github_url = typer.prompt("GitHub URL", default=p.github_url or "")
    p.resume_path = typer.prompt("Resume file path", default=p.resume_path or "")
    while True:
        exp = typer.prompt("Years of experience", default=str(p.years_of_experience or 0))
        parsed_exp = _parse_years_of_experience(exp)
        if parsed_exp is not None:
            p.years_of_experience = parsed_exp
            break
        console.print("[yellow]Couldn't read a number from that — enter digits, like 5.[/yellow]")
    p.current_title = typer.prompt("Current job title", default=p.current_title or "")

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
        help="Bind address. Loopback by default (safe). The dashboard serves "
        "your name/email/phone with no authentication, so only widen this "
        "deliberately — e.g. --host 100.x.y.z for a specific Tailscale IP. "
        "Avoid 0.0.0.0, which exposes your PII to everyone on the network.",
    ),
    port: Optional[int] = typer.Option(
        None,
        help="Port (default: 8767 — EYE uses 8766)",
    ),
):
    """Start the remote dashboard (access from phone via Tailscale).

    Binds to loopback (127.0.0.1) by default. To reach it from your phone over
    Tailscale, pass your machine's Tailscale IP explicitly with --host.
    """
    from jobpilot.core.config import DEFAULT_SERVE_PORT
    from jobpilot.core.server import run_server, get_tailscale_ip, get_local_ip

    serve_port = port or DEFAULT_SERVE_PORT
    ts_ip = get_tailscale_ip()
    lan_ip = get_local_ip()

    console.print(Panel.fit(
        f"[bold cyan]🚀 JobPilot Remote Dashboard[/bold cyan]\n"
        + (f"[bold green]Tailscale:[/bold green]  http://{ts_ip}:{serve_port}\n" if ts_ip else "[yellow]Tailscale not detected — start it with `tailscale up`[/yellow]\n")
        + f"[bold]LAN:[/bold]        http://{lan_ip}:{serve_port}\n"
        + f"[dim]Local:       http://127.0.0.1:{serve_port}[/dim]\n"
        + f"\n[dim]Open the Tailscale URL on your phone to apply remotely.[/dim]\n"
        + f"[dim]Keep this terminal open. Ctrl+C to stop.[/dim]",
        border_style="cyan",
        title="Mobile Access",
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
        watch_hud(console, opts=opts, interval=interval, verbose=verbose)
    else:
        render_hud(console, opts=opts, verbose=verbose)


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
    from jobpilot.ui.radar import RadarOptions, render_radar, watch_radar

    opts = RadarOptions(
        austin=austin,
        contract_first=contract_first,
        drop_rigid_schedule=anti_schedule,
        gigs_top=gigs_top,
        jobs_limit=jobs_limit,
        min_gig_score=min_gig_score,
    )
    if watch:
        watch_radar(console, opts=opts, interval=interval)
    else:
        render_radar(console, opts=opts)


@app.command()
def board(
    fresh: bool = typer.Option(True, "--fresh/--all", help="Show only queued roles (default) or full queue"),
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
def queue(
    refresh: bool = typer.Option(False, "--refresh", "-r", help="Re-scan all portals and rebuild queue"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max jobs to queue"),
    open_dashboard: bool = typer.Option(True, "--open/--no-open", help="Open dashboard in browser after building"),
    fresh: bool = typer.Option(False, "--fresh", help="Show only roles you haven't applied to / been rejected from (dedup vs applications.db)"),
    as_json: bool = typer.Option(False, "--json", help="Emit the queue (filtered) as JSON to stdout. Suppresses dashboard + console table. For piping into other tools/agents."),
    no_board: bool = typer.Option(False, "--no-board", help="Skip the terminal board after queue build/load"),
):
    """Scan ATS boards, score jobs, and open the apply dashboard."""
    from jobpilot.core.queue_builder import build_queue, load_queue, save_queue
    from jobpilot.ui.terminal_board import BoardFilters, render_board
    import subprocess
    from dataclasses import asdict

    dashboard_path = Path(__file__).parent / "ui" / "dashboard.html"

    # JSON mode: emit raw queue (respecting --fresh and --limit) and exit.
    # Designed for cross-agent / scripting use without dashboard/UI side effects.
    if as_json:
        if refresh or not (Path(__file__).parent / "data" / "queue.json").exists():
            jobs = build_queue(limit=limit)
            save_queue(jobs)
        else:
            jobs = load_queue()
        view = [j for j in jobs if j.status == "queued"] if fresh else jobs
        view = view[:limit]
        console.print_json(data=[asdict(j) for j in view])
        return

    if not refresh and (Path(__file__).parent / "data" / "queue.json").exists():
        jobs = load_queue()
        queued = [j for j in jobs if j.status == "queued"]
        console.print(f"[cyan]Loaded existing queue: {len(queued)} jobs ready to apply[/cyan]")
        if not queued:
            console.print(f"[dim]Run with --refresh to re-scan portals[/dim]")
    else:
        console.print("[cyan]Scanning ATS boards (this takes ~30 seconds)...[/cyan]")
        jobs = build_queue(limit=limit)
        if not jobs:
            console.print("[yellow]No jobs found. Check data/portals.json and your internet connection.[/yellow]")
            raise typer.Exit(1)
        save_queue(jobs)
        console.print(f"[green]✓ Built queue: {len(jobs)} jobs across tech + field ops[/green]")

    if not no_board:
        render_board(
            console,
            filters=BoardFilters(
                fresh=True,
                limit=min(limit, 20),
            ),
        )

    if open_dashboard and dashboard_path.exists():
        subprocess.run(["open", str(dashboard_path)], check=False)
        console.print(f"[green]✓ Web dashboard opened — or run [cyan]jobpilot board --watch[/cyan] in another tab[/green]")
    elif not dashboard_path.exists():
        console.print(f"[yellow]Dashboard not found at {dashboard_path}[/yellow]")


@app.command()
def apply(
    job_id: str = typer.Argument(..., help="Job ID from the queue (shown in dashboard or 'jobpilot queue')"),
    port: int = typer.Option(9222, help="Chrome debugging port"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be filled without filling"),
    claim_approved: bool = typer.Option(False, "--claim-approved", help="Use only if you've already approved staging this Claude-ready target"),
):
    """Auto-fill a job application. Stops before submit — you approve."""
    from jobpilot.core.queue_builder import get_job, update_job_status
    from jobpilot.core.form_filler import fill_application

    job = get_job(job_id)
    if not job:
        console.print(f"[red]Job '{job_id}' not found in queue. Run 'jobpilot queue' first.[/red]")
        raise typer.Exit(1)

    if job.status in {"applied", "submitted"}:
        console.print(f"[yellow]Already applied to {job.title} @ {job.company}[/yellow]")
        if not typer.confirm("Apply again anyway?"):
            raise typer.Exit(0)

    console.print(Panel.fit(
        f"[bold cyan]{job.title}[/bold cyan]\n"
        f"[white]{job.company}[/white]  •  {job.location}\n"
        f"[dim]{job.url}[/dim]\n"
        f"[bold]Fit Score: {job.fit_score}/100[/bold]  •  Track: {job.track}",
        border_style="cyan",
        title="Applying to",
    ))

    if dry_run:
        console.print("[dim]Dry run — no form filling. Pass without --dry-run to apply.[/dim]")
        raise typer.Exit(0)

    _enforce_claim_lock(job, claim_approved=claim_approved)

    profile = get_profile_store().load()

    console.print("[cyan]Starting LLM form filler...[/cyan]")
    console.print("[dim]Chrome will navigate to the job page. Watch it fill the form.[/dim]")
    console.print("[yellow]⚠  It will STOP before submitting. You approve in this terminal.[/yellow]\n")

    result = asyncio.run(fill_application(
        job_url=job.url,
        job_title=job.title,
        company=job.company,
        profile=profile,
        cdp_port=port,
    ))

    if not result.success:
        console.print(f"[red]Form filler failed: {result.error}[/red]")
        raise typer.Exit(1)

    console.print("\n[bold green]✓ Form filled. Review what was filled:[/bold green]")
    for f in result.filled_fields:
        console.print(f"  [green]•[/green] {f}")

    console.print("\n[bold yellow]Review the form in Chrome now.[/bold yellow]")
    console.print("Make any corrections in the browser, then come back here.\n")

    confirmed = typer.confirm("Everything looks good? Submit the application?", default=False)

    if confirmed:
        console.print("[cyan]Go to Chrome and click the Submit button.[/cyan]")
        console.print("[dim](JobPilot never auto-submits — that's your call.)[/dim]")
        input("Press Enter once you've submitted in Chrome...")
        update_job_status(job_id, "applied")
        console.print("[bold green]🎊 Marked as applied! Keep going.[/bold green]")
    else:
        console.print("[dim]Not submitted. Job stays in queue.[/dim]")


@app.command()
def report(
    csv_export: bool = typer.Option(False, "--csv", help="Export to CSV file"),
    days: int = typer.Option(7, "--days", "-d", help="Number of days to include"),
):
    """Generate application analytics report"""
    from jobpilot.core.analytics import export_csv, daily_digest

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
    from rich.table import Table
    from rich.prompt import Prompt, Confirm
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
            console.print(f"  [green]✓ Updated[/green]")
        elif action == "delete":
            if Confirm.ask(f"  Really delete?", default=False):
                db.delete_template(t["question"])
                console.print(f"  [red]✗ Deleted[/red]")
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
    import subprocess as _sp

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
            console.print(f"[cyan]→ on clipboard. Cmd+V in the field, then Enter where you want paragraph breaks.[/cyan]")
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
    import subprocess as _sp

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
    console.print(f"[dim]Cmd+V in the field, then Enter where you want paragraph breaks.[/dim]")


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
    console.print(f"[dim]Copy any of these with: jobpilot answer copy <company> <question>[/dim]")


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
def psyche(
    sample: bool = typer.Option(True, "--sample/--no-sample",
                                help="Show the top scoring breakdown for current queue.json"),
):
    """View your work-style profile + how it's scoring real jobs.

    Reads `data/psyche_profile.json`. Edit that file directly to retune.
    Fork it for your own preferences — JobPilot's psycho-fit dimension
    (15 of 100 total points) is driven entirely by what's in there.
    """
    from jobpilot.core.queue_builder import (
        _load_psyche_profile, _score_psyche_fit, _load_portal_notes,
        MOAT_COMPANY_TAGS, PSYCHE_PROFILE_PATH,
    )
    from rich.panel import Panel
    from rich.table import Table

    profile = _load_psyche_profile()
    user = profile.get("user", "(unset)")
    dims = profile.get("dimensions", {}) or {}

    console.print(Panel.fit(
        f"[bold]Psyche profile:[/bold] {user}\n"
        f"[dim]{PSYCHE_PROFILE_PATH}[/dim]",
        title="🧠 jobpilot psyche",
    ))

    if dims:
        dim_table = Table(title="Dimensions", show_header=True, header_style="bold")
        dim_table.add_column("Dimension", style="cyan")
        dim_table.add_column("Value", style="white")
        for k, v in dims.items():
            dim_table.add_row(k, str(v))
        console.print(dim_table)

    loved = profile.get("loved_signals", {}) or {}
    hated = profile.get("hated_signals", {}) or {}
    sig_table = Table(title="Signal counts", show_header=True, header_style="bold")
    sig_table.add_column("Bucket", style="cyan")
    sig_table.add_column("Loved", style="green")
    sig_table.add_column("Hated", style="red")
    for bucket in ("title", "company_or_note", "industry"):
        sig_table.add_row(
            bucket,
            f"{len(loved.get(bucket, []) or [])} tokens",
            f"{len(hated.get(bucket, []) or [])} tokens",
        )
    console.print(sig_table)

    if not sample:
        return

    queue_path = Path(__file__).parent / "data" / "queue.json"
    if not queue_path.exists():
        console.print("[yellow]No queue.json yet — run `jobpilot queue --refresh` first to see scoring in action.[/yellow]")
        return

    queue = json.loads(queue_path.read_text())
    queue.sort(key=lambda j: -j.get("psyche_score", 0))
    top = queue[:8]
    sample_table = Table(title="Top 8 by Psycho-fit (live queue)", show_header=True, header_style="bold")
    sample_table.add_column("Psy", style="magenta", width=4)
    sample_table.add_column("Total", style="cyan", width=6)
    sample_table.add_column("Company", style="white", max_width=18)
    sample_table.add_column("Role", style="green", max_width=42)
    sample_table.add_column("Status", style="yellow", width=10)
    for j in top:
        sample_table.add_row(
            str(j.get("psyche_score", 0)),
            str(j.get("fit_score", 0)),
            j["company"],
            j["title"][:42],
            j["status"],
        )
    console.print(sample_table)
    console.print(
        f"[dim]Edit {PSYCHE_PROFILE_PATH} and re-run `jobpilot queue --refresh` "
        f"to see scoring shift.[/dim]"
    )


@app.command()
def log(
    company: str = typer.Argument(..., help="Company name (used for dedup)"),
    title: str = typer.Option("", "--title", "-t", help="Role title"),
    url: str = typer.Option("", "--url", "-u", help="Apply URL (used as primary dedup key)"),
    status: str = typer.Option("applied", "--status", "-s",
                               help="applied | submitted | rejected | interview | abandoned"),
    date: Optional[str] = typer.Option(None, "--date", "-d", help="ISO date (defaults to today)"),
):
    """Log an application to the tracker.

    Closes the gap where manual/external applies (Ashby, Lever direct, etc.)
    don't auto-write to applications.db, so subsequent `queue --fresh` runs
    correctly dedup them. Idempotent on URL.
    """
    tracker = get_application_tracker()
    try:
        app_row = tracker.log_application(
            company=company,
            title=title,
            url=url,
            status=status,
            applied_at=date,
            source="manual-log",
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    stats = tracker.get_stats()
    console.print(
        f"[green]✓ Tracked: {app_row.company} — {app_row.job_title or '(role)'} "
        f"[{app_row.status}][/green]"
    )
    console.print(f"[dim]tracker now: {stats['total']} rows[/dim]")
    tracker.close()


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
