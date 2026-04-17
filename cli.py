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
from jobpilot.core.bro_client import get_health
from jobpilot.core.jd_parser import JDParser
from jobpilot.core.job_scorer import JobScorer
from jobpilot.core.portal_scanner import PortalScanner, ScanTarget
from jobpilot.core.doctor import run_doctor
from jobpilot.core.resume_tailor import ResumeTailor
from jobpilot.learning.action_recorder import get_action_recorder

log = get_logger(__name__)

app = typer.Typer(
    name="jobpilot",
    help="Semi-automated LinkedIn job application copilot",
)
console = Console()


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
        return candidate.read_text(), str(candidate)
    return source, "inline text"


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

    if bro:
        table.add_row(
            "Bro API",
            "OK" if bro_ok else "WARN",
            "AI backend reachable" if bro_ok else "Bro is unreachable; chat/advice will be degraded",
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

    if bro and not bro_ok:
        console.print("\n[yellow]Bro is down. Start the AI stack if you want chat, RAG, and voice help.[/yellow]")
        return 1

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

    asyncio.run(_start_async(port, watch))


async def _start_async(port: int, watch: bool):
    """Bootstrap services, wire events, delegate to engine."""
    console.print("\n[cyan]Connecting to Chrome...[/cyan]")
    bridge = await connect_to_chrome(port)

    if not bridge:
        console.print("\n[yellow]💡 Tip: Run ./scripts/launch_chrome.sh first[/yellow]")
        return

    # Auto-index resume
    _check_and_index_resume()

    # Inject overlays
    from jobpilot.ui.ghost_overlay import GhostOverlay
    from jobpilot.ui.chat_overlay import ChatOverlay

    overlay = GhostOverlay(bridge.page)
    await overlay.inject()

    chat_overlay = ChatOverlay(bridge.page)
    await chat_overlay.inject()

    # Wire event bus
    events = EventBus()
    _wire_events(events)

    # Assemble engine
    engine = ApplicationEngine(
        bridge=bridge,
        events=events,
        overlay=overlay,
        chat_overlay=chat_overlay,
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
    bro: bool = typer.Option(True, "--bro/--no-bro", help="Include the Bro health check"),
):
    """Check Chrome, Bro, and JobPilot's local data health."""
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
    bro: bool = typer.Option(True, "--bro/--no-bro", help="Use Bro/RAG for more personalized tailoring when available"),
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
    exp = typer.prompt("Years of experience", default=str(p.years_of_experience) or "0")
    p.years_of_experience = int(exp) if exp.isdigit() else 0
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
        f"\n[bold]Application Summary:[/bold] {s['submitted']} submitted, "
        f"{s['abandoned']} abandoned, {s['in_progress']} in progress\n"
    )
    tracker.display_recent(limit)
    tracker.close()


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


if __name__ == "__main__":
    app()
