"""Human-friendly panes for the iTerm command center (non-programmer view)."""

from __future__ import annotations

import re
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel

from jobpilot.core.config import DATA_DIR, DEFAULT_SERVE_PORT

from jobpilot.ui.income_data import IncomeViewOptions, load_gigs, load_jobs, load_pipeline_rows
from jobpilot.ui.view_helpers import check_chrome, check_dashboard, materials_ready

OUTREACH_DIR = DATA_DIR / "outreach" / "ready-to-send"
SERVE_LOG = Path.home() / ".jobpilot" / "serve.log"
AUSTIN_ARRIVAL = date(2026, 6, 30)


def _austin_countdown() -> str:
    today = date.today()
    days = (AUSTIN_ARRIVAL - today).days
    if days > 0:
        return f"{days}d to Austin"
    if days == 0:
        return "Austin today"
    return f"Austin +{-days}d"

_LOG_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Uvicorn running", re.I), "Dashboard is online"),
    (re.compile(r"Application startup complete", re.I), "Dashboard finished starting"),
    (re.compile(r"GET /api/queue", re.I), "Job list was viewed"),
    (re.compile(r"POST /api/queue/refresh", re.I), "Refreshing job listings…"),
    (re.compile(r"POST /api/job/[^/]+/mark-applied", re.I), "You marked an application as submitted"),
    (re.compile(r"POST /api/job/[^/]+/opened", re.I), "You opened a job posting"),
    (re.compile(r"POST /api/job/[^/]+/skip", re.I), "You skipped a job"),
    (re.compile(r"POST /api/applications/log", re.I), "Application activity logged"),
    (re.compile(r"ERROR|Traceback|Exception", re.I), "Something needs attention — check the dashboard"),
    (re.compile(r"Shutting down", re.I), "Dashboard stopped"),
    (re.compile(r"Started server process", re.I), "Dashboard process started"),
]


def _friendly_pipeline_status(status: str) -> str:
    return {
        "new": "New lead",
        "saved": "Saved",
        "drafted": "Draft ready",
        "sent": "Message sent",
        "replied": "They replied",
        "interview": "Interview",
        "hired": "Hired",
        "passed": "Passed",
    }.get(status, status.replace("_", " ").title())


def _outreach_packages() -> list[tuple[str, str]]:
    if not OUTREACH_DIR.is_dir():
        return []
    rows: list[tuple[str, str]] = []
    for d in sorted(OUTREACH_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        label = d.name
        if "-" in label:
            _, rest = label.split("-", 1)
            label = rest.replace("-", " ").title()
        rows.append((d.name, label))
    return rows


def _top_gig_name() -> Optional[str]:
    gigs, _ = load_gigs(IncomeViewOptions(gigs_fresh_only=True))
    if not gigs:
        return None
    g = gigs[0]
    return f"{g.company} — {g.title}"


def _top_job_with_materials() -> Optional[str]:
    jobs = load_jobs(IncomeViewOptions())
    for j in jobs:
        if materials_ready(j.company):
            return f"{j.company} — {j.title}"
    if jobs:
        j = jobs[0]
        return f"{j.company} — {j.title}"
    return None


def _serve_url(port: int = DEFAULT_SERVE_PORT) -> str:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/profile", timeout=1.5) as resp:
            if resp.status == 200:
                return f"http://127.0.0.1:{port}/"
    except Exception:
        pass
    return f"http://127.0.0.1:{port}/"


def build_status_panel(port: int = DEFAULT_SERVE_PORT) -> Panel:
    dash_ok = check_dashboard(port)
    chrome_ok = check_chrome()
    gigs, gigs_meta = load_gigs(IncomeViewOptions(gigs_fresh_only=True))
    jobs = load_jobs(IncomeViewOptions())
    pipeline = load_pipeline_rows(IncomeViewOptions())
    outreach = _outreach_packages()

    lines: list[str] = []
    if dash_ok and chrome_ok:
        lines.append("[bold green]All set — JobPilot is ready[/bold green]")
    elif dash_ok:
        lines.append("[bold yellow]Dashboard is up — browser helper still starting[/bold yellow]")
    else:
        lines.append("[bold red]Starting up — give it a few seconds[/bold red]")

    lines.append("")
    lines.append("[bold]Services[/bold]")
    lines.append(
        f"  {'[green]✓[/green]' if dash_ok else '[red]✗[/red]'} "
        f"Phone & laptop dashboard  [dim]{_serve_url(port)}[/dim]"
    )
    lines.append(
        f"  {'[green]✓[/green]' if chrome_ok else '[red]✗[/red]'} "
        "Browser helper (fills applications for you)"
    )

    lines.append("")
    lines.append("[bold]Your search right now[/bold]")
    fresh = gigs_meta.get("fresh_count", gigs_meta.get("shown", len(gigs)))
    lines.append(f"  • [cyan]{len(gigs)}[/cyan] contract gigs to review ({fresh} new today)")
    ready_jobs = sum(1 for j in jobs if materials_ready(j.company))
    lines.append(
        f"  • [cyan]{len(jobs)}[/cyan] Austin / remote jobs queued"
        + (f" ([green]{ready_jobs}[/green] with application kit ready)" if ready_jobs else "")
    )
    if pipeline:
        lines.append(f"  • [cyan]{len(pipeline)}[/cyan] active applications in your pipeline")
    if outreach:
        lines.append(f"  • [cyan]{len(outreach)}[/cyan] outreach packages ready to send")

    lines.append("")
    lines.append("[bold]Suggested next step[/bold]")
    if outreach:
        _, name = outreach[0]
        lines.append(f"  → Send outreach: [green]{name}[/green]  [dim](type jps 01)[/dim]")
    elif ready_jobs:
        top = _top_job_with_materials()
        if top:
            lines.append(f"  → Apply with your kit: [green]{top}[/green]")
    elif gigs:
        top_gig = _top_gig_name()
        if top_gig:
            lines.append(f"  → Review top gig: [green]{top_gig}[/green]")
    else:
        lines.append("  → Run [cyan]jp queue --refresh[/cyan] to scan for new leads")

    lines.append("")
    lines.append(f"[dim]{_austin_countdown()} · updates every 30s[/dim]")

    return Panel(
        "\n".join(lines),
        title="[bold cyan]JobPilot[/bold cyan] — Status",
        border_style="cyan",
        box=box.ROUNDED,
    )


def _translate_log_line(line: str) -> Optional[str]:
    raw = line.strip()
    if not raw:
        return None
    for pat, msg in _LOG_RULES:
        if pat.search(raw):
            return msg
    if "INFO:" in raw and "/api/" in raw:
        return "Dashboard activity"
    return None


def _read_activity_messages(log_path: Path = SERVE_LOG, *, limit: int = 12) -> list[str]:
    if not log_path.is_file():
        return ["[dim]Waiting for activity…[/dim]"]
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ["[dim]Could not read activity log[/dim]"]

    messages: list[str] = []
    for line in reversed(lines[-200:]):
        msg = _translate_log_line(line)
        if not msg:
            continue
        ts = ""
        m = re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})", line)
        if m:
            try:
                dt = datetime.fromisoformat(m.group(1).replace(" ", "T"))
                ts = dt.strftime("%I:%M %p").lstrip("0")
            except ValueError:
                ts = ""
        entry = f"[dim]{ts}[/dim]  {msg}" if ts else msg
        if entry not in messages:
            messages.append(entry)
        if len(messages) >= limit:
            break
    if not messages:
        return ["[dim]No recent activity — open the dashboard or review leads[/dim]"]
    return list(reversed(messages))


def build_activity_panel() -> Panel:
    body = "\n".join(_read_activity_messages())
    return Panel(
        body,
        title="Recent activity",
        border_style="dim",
        box=box.ROUNDED,
    )


def watch_status_board(console: Console, *, interval: float = 30.0, port: int = DEFAULT_SERVE_PORT) -> None:
    with Live(console=console, refresh_per_second=2, screen=True) as live:
        while True:
            live.update(build_status_panel(port=port))
            time.sleep(interval)


def watch_activity_feed(console: Console, *, interval: float = 5.0) -> None:
    with Live(console=console, refresh_per_second=2, screen=True) as live:
        while True:
            live.update(build_activity_panel())
            time.sleep(interval)


def render_status_board(console: Console, port: int = DEFAULT_SERVE_PORT) -> None:
    console.print(build_status_panel(port=port))


def render_activity_feed(console: Console) -> None:
    console.print(build_activity_panel())