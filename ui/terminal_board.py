"""Rich terminal dashboard for JobPilot queue + tracker state."""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from rich import box
from rich.columns import Columns
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from jobpilot.core.application_tracker import get_application_tracker
from jobpilot.core.config import DATA_DIR, DEFAULT_SERVE_PORT
from jobpilot.core.profile_store import get_profile_store
from jobpilot.core.queue_builder import QueueJob, load_queue

ANSWERS_DIR = DATA_DIR / "answers"


def score_bar(score: int, width: int = 10) -> Text:
    """Render a 0-100 score as a colored block bar."""
    score = max(0, min(100, int(score or 0)))
    filled = round(score / 100 * width)
    bar = Text()
    for i in range(width):
        if i < filled:
            if score >= 75:
                bar.append("█", style="bold green")
            elif score >= 55:
                bar.append("█", style="cyan")
            elif score >= 40:
                bar.append("█", style="yellow")
            else:
                bar.append("█", style="dim")
        else:
            bar.append("░", style="dim")
    bar.append(f" {score}", style="bold")
    return bar


def _status_style(status: str) -> str:
    return {
        "queued": "bold green",
        "viewing": "bold cyan",
        "applied": "yellow",
        "submitted": "bold green",
        "rejected": "red",
        "skipped": "dim",
        "interview": "bold magenta",
    }.get(status or "", "white")


def _company_slug(company: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (company or "").strip().lower()).strip("-")


def _materials_ready(company: str) -> Optional[Path]:
    slug = _company_slug(company)
    if not slug:
        return None
    company_dir = ANSWERS_DIR / slug
    if not company_dir.is_dir():
        # Also try compact slug (no hyphens), e.g. luxurypresence
        compact = slug.replace("-", "")
        alt = ANSWERS_DIR / compact
        company_dir = alt if alt.is_dir() else company_dir
    if not company_dir.is_dir():
        return None
    paste = company_dir / "PASTE_SHEET.txt"
    if paste.exists():
        return paste
    for candidate in sorted(company_dir.glob("*.md")):
        return candidate
    return None


def _location_bucket(location: str) -> str:
    loc = (location or "").lower()
    if "austin" in loc:
        return "Austin"
    if "remote" in loc:
        return "Remote"
    if any(x in loc for x in ("new york", "nyc")):
        return "NYC"
    if any(x in loc for x in ("san francisco", "bay area", "sf", "menlo", "palo alto")):
        return "Bay Area"
    if loc in {"", "not specified"}:
        return "Unspecified"
    return "Other"


@dataclass
class BoardFilters:
    fresh: bool = False
    austin: bool = False
    autonomous: bool = False
    location: str = ""
    status: str = "queued"
    limit: int = 20


def _is_senior_title(title: str) -> bool:
    t = (title or "").lower()
    return "senior" in t and not any(x in t for x in ("manager", "principal", "staff", "director", "vp"))


def filter_jobs(jobs: list[QueueJob], filters: BoardFilters) -> list[QueueJob]:
    view = list(jobs)
    if filters.fresh:
        view = [j for j in view if j.status == "queued"]
    elif filters.status and filters.status != "all":
        view = [j for j in view if j.status == filters.status]
    if filters.autonomous:
        view = [j for j in view if not _is_senior_title(j.title)]
    if filters.austin:
        view = [j for j in view if "austin" in (j.location or "").lower()]
    if filters.location:
        needle = filters.location.lower()
        view = [j for j in view if needle in (j.location or "").lower()]
    view.sort(key=lambda j: (j.fit_score, j.psyche_score), reverse=True)
    return view[: filters.limit]


def _stats_panel(jobs: list[QueueJob]) -> Panel:
    counts = Counter(j.status for j in jobs)
    loc_counts = Counter(_location_bucket(j.location) for j in jobs if j.status == "queued")
    lines = [
        f"[bold]Queue[/bold]  [green]{counts.get('queued', 0)}[/] ready  "
        f"[yellow]{counts.get('applied', 0)}[/] applied  "
        f"[red]{counts.get('rejected', 0)}[/] rejected  "
        f"[cyan]{counts.get('submitted', 0)}[/] submitted",
    ]
    if loc_counts:
        loc_bits = "  ".join(f"{k}: {v}" for k, v in loc_counts.most_common(5))
        lines.append(f"[dim]Locations (queued):[/dim] {loc_bits}")
    return Panel("\n".join(lines), title="Pipeline", border_style="blue", box=box.ROUNDED)


def _tracker_panel(stats: dict[str, Any]) -> Panel:
    return Panel(
        f"[bold]{stats.get('total', 0)}[/] tracked  "
        f"[green]{stats.get('submitted', 0)}[/] submitted  "
        f"[magenta]{stats.get('interview', 0)}[/] interview  "
        f"[yellow]{stats.get('in_progress', 0)}[/] in progress",
        title="Tracker",
        border_style="magenta",
        box=box.ROUNDED,
    )


def _services_panel(*, dashboard_up: bool, chrome_up: bool, port: int) -> Panel:
    dash = "[green]up[/green]" if dashboard_up else "[red]down[/red]"
    chrome = "[green]up[/green]" if chrome_up else "[red]down[/red]"
    return Panel(
        f"Dashboard ({port}): {dash}   Chrome CDP (9222): {chrome}",
        title="Services",
        border_style="dim",
        box=box.ROUNDED,
    )


def _next_up_panel(jobs: list[QueueJob]) -> Optional[Panel]:
    queued = [j for j in jobs if j.status == "queued"]
    if not queued:
        return Panel(
            "[yellow]No queued roles.[/yellow] Run [cyan]jobpilot queue --refresh[/cyan].",
            title="Next Up",
            border_style="yellow",
        )
    top = max(queued, key=lambda j: (j.fit_score, j.psyche_score))
    materials = _materials_ready(top.company)
    mat_line = (
        f"[green]Materials ready:[/green] {materials}"
        if materials
        else "[dim]No paste sheet yet — run answer draft + make_paste_sheet[/dim]"
    )
    body = (
        f"[bold cyan]{top.company}[/bold cyan] — [bold]{top.title}[/bold]\n"
        f"{score_bar(top.fit_score)}  psyche {top.psyche_score}/15  ·  {top.location}\n"
        f"[dim]{top.url}[/dim]\n"
        f"ID [bold]{top.id}[/bold]  ·  portal {top.portal}\n"
        f"{mat_line}\n"
        f"[dim]Apply:[/dim] open URL in your browser · paste from sheet · [cyan]jobpilot log {top.company} -t \"{top.title}\" -u \"{top.url}\"[/cyan]"
    )
    return Panel(body, title="Next Up", border_style="green", box=box.HEAVY)


def _queue_table(jobs: list[QueueJob], *, title: str) -> Table:
    table = Table(
        title=title,
        show_header=True,
        header_style="bold",
        box=box.SIMPLE_HEAVY,
        expand=True,
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Fit", width=14)
    table.add_column("Company", style="white", max_width=16)
    table.add_column("Role", style="green", max_width=34)
    table.add_column("Location", style="magenta", max_width=18)
    table.add_column("Psy", justify="right", width=4)
    table.add_column("Status", width=10)
    table.add_column("ID", style="dim", width=8)
    table.add_column("📋", width=3, justify="center")

    for i, job in enumerate(jobs, 1):
        ready = "✓" if _materials_ready(job.company) else "·"
        ready_style = "green" if ready == "✓" else "dim"
        table.add_row(
            str(i),
            score_bar(job.fit_score),
            job.company,
            job.title[:34],
            (job.location or "—")[:18],
            str(job.psyche_score),
            Text(job.status, style=_status_style(job.status)),
            job.id,
            Text(ready, style=ready_style),
        )
    return table


def _recent_table(rows: list[Any], limit: int = 6) -> Table:
    table = Table(title=f"Recent Applications (last {limit})", box=box.SIMPLE, expand=True)
    table.add_column("Date", style="dim", width=10)
    table.add_column("Company", max_width=14)
    table.add_column("Role", style="green", max_width=30)
    table.add_column("Status", width=10)
    for row in rows[:limit]:
        date = (row.applied_at or "")[:10]
        title = (row.job_title or "")[:30]
        table.add_row(
            date,
            row.company or "—",
            title,
            Text(row.status, style=_status_style(row.status)),
        )
    return table


def _filter_caption(filters: BoardFilters) -> str:
    bits = []
    if filters.autonomous:
        bits.append("no-senior")
    if filters.austin:
        bits.append("Austin")
    if filters.location:
        bits.append(f'location~"{filters.location}"')
    if filters.fresh:
        bits.append("fresh")
    else:
        bits.append(f"status={filters.status}")
    bits.append(f"top {filters.limit}")
    return " · ".join(bits)


def _check_dashboard(port: int) -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/queue", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _check_chrome(port: int = 9222) -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5):
            return True
    except Exception:
        return False


def build_board_renderable(
    *,
    filters: Optional[BoardFilters] = None,
    serve_port: int = DEFAULT_SERVE_PORT,
) -> RenderableType:
    """Assemble the full terminal board as a Rich renderable."""
    filters = filters or BoardFilters()
    jobs = load_queue()
    if not jobs:
        return Panel(
            "[yellow]Queue is empty.[/yellow] Run [cyan]jobpilot queue --refresh[/cyan] first.",
            title="JobPilot Board",
            border_style="cyan",
        )

    profile = get_profile_store().load()
    tracker = get_application_tracker()
    try:
        stats = tracker.get_stats()
        recent = tracker.get_recent(8)
    finally:
        tracker.close()

    view = filter_jobs(jobs, filters)
    header = Panel(
        f"[bold]{profile.first_name} {profile.last_name}[/bold]  "
        f"[dim]{profile.city}, {profile.state}[/dim]\n"
        f"[dim]{datetime.now().strftime('%A %b %d, %Y · %H:%M')}[/dim]  "
        f"[dim]filter: {_filter_caption(filters)}[/dim]",
        title="[bold cyan]JobPilot Board[/bold cyan]",
        subtitle="[dim]Web: http://127.0.0.1:{port}/ · CLI: jobpilot board --watch[/dim]".format(
            port=serve_port,
        ),
        border_style="cyan",
        box=box.DOUBLE,
    )

    top_row = Columns(
        [
            _stats_panel(jobs),
            _tracker_panel(stats),
            _services_panel(
                dashboard_up=_check_dashboard(serve_port),
                chrome_up=_check_chrome(),
                port=serve_port,
            ),
        ],
        equal=True,
        expand=True,
    )

    parts: list[RenderableType] = [
        header,
        top_row,
        _next_up_panel(jobs),
    ]

    if view:
        parts.append(_queue_table(view, title=f"Queue — {_filter_caption(filters)}"))
    else:
        parts.append(
            Panel(
                "[yellow]No jobs match this filter.[/yellow] Try [cyan]--status all[/cyan] or [cyan]--refresh[/cyan].",
                title="Queue",
            )
        )

    if recent:
        parts.append(_recent_table(recent))

    parts.append(
        Panel(
            "[dim]Commands:[/dim] "
            "[cyan]jobpilot board --austin --watch[/cyan] · "
            "[cyan]jobpilot queue --refresh --no-open[/cyan] · "
            "[cyan]jobpilot score <jd.txt>[/cyan] · "
            "[cyan]jobpilot answer draft <co> \"question\"[/cyan]",
            border_style="dim",
            box=box.ROUNDED,
        )
    )
    return Group(*parts)


def render_board(
    console: Console,
    *,
    filters: Optional[BoardFilters] = None,
    serve_port: int = DEFAULT_SERVE_PORT,
) -> None:
    """Print the terminal board once."""
    console.print(build_board_renderable(filters=filters, serve_port=serve_port))


def watch_board(
    console: Console,
    *,
    filters: Optional[BoardFilters] = None,
    interval: float = 5.0,
    serve_port: int = DEFAULT_SERVE_PORT,
) -> None:
    """Live-refreshing terminal board (Ctrl+C to stop)."""
    from rich.live import Live

    with Live(console=console, refresh_per_second=4, screen=True) as live:
        try:
            while True:
                live.update(
                    build_board_renderable(filters=filters, serve_port=serve_port)
                )
                time.sleep(interval)
        except KeyboardInterrupt:
            pass