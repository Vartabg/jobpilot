"""Rich terminal dashboard for JobPilot queue + tracker state."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from rich import box
from rich.columns import Columns
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from jobpilot.core import queue_builder
from jobpilot.core.application_tracker import get_application_tracker
from jobpilot.core.config import DEFAULT_SERVE_PORT
from jobpilot.core.profile_store import get_profile_store
from jobpilot.core.queue_builder import QueueJob
from jobpilot.ui.view_helpers import (
    check_chrome,
    check_dashboard,
    is_senior_title,
    materials_ready,
)

# Backward-compatible private aliases (tests and legacy imports).
_is_senior_title = is_senior_title
_materials_ready = materials_ready
_check_dashboard = check_dashboard
_check_chrome = check_chrome


_ACTIVE_QUEUE_STATUSES = frozenset({"queued", "viewing"})
_DECISION_RANK = {
    "apply_now": 4,
    "stretch": 3,
    "investigate": 2,
    "skip": 1,
}
_LEGITIMACY_RANK = {
    "recommend": 4,
    "review": 3,
    "hold": 2,
    "block": 1,
}


def _evidence_number(value: Any) -> int:
    """Normalize persisted evidence values without promoting missing data."""
    if value is None or value == "":
        return -1
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _is_apply_ready(job: QueueJob) -> bool:
    """Delegate readiness to the queue gate and fail closed for legacy rows."""
    try:
        return queue_builder.is_apply_ready(job)
    except (AttributeError, TypeError, ValueError):
        return False


def _evidence_sort_key(job: QueueJob) -> tuple[int, int, int, int, int, int]:
    """Rank independent evidence axes, using fit only as the final tie-breaker."""
    return (
        _DECISION_RANK.get(str(getattr(job, "decision", "")), 0),
        _LEGITIMACY_RANK.get(str(getattr(job, "legitimacy_state", "")), 0),
        _evidence_number(getattr(job, "qualification_lower_bound", None)),
        _evidence_number(getattr(job, "evidence_coverage", None)),
        _evidence_number(getattr(job, "work_context_match", None)),
        _evidence_number(getattr(job, "fit_score", None)),
    )


def _readable_label(value: Any, fallback: str = "unknown") -> str:
    normalized = str(value or "").strip().replace("_", " ")
    return normalized or fallback


def _axis_text(value: Any, *, compact: bool = False) -> str:
    number = _evidence_number(value)
    if number < 0:
        return "?" if compact else "unknown"
    return str(number) if compact else f"{number}/100"


def _coverage_text(value: Any) -> str:
    number = _evidence_number(value)
    return "?" if number < 0 else f"{number}%"


def _legitimacy_text(job: QueueJob) -> str:
    grade = str(getattr(job, "evidence_grade", "") or "?").upper()
    state = _readable_label(getattr(job, "legitimacy_state", ""))
    return f"{grade}/{state}"


def _evidence_note(job: QueueJob) -> str:
    gap = str(getattr(job, "biggest_gap", "") or "").strip()
    if gap:
        return f"Biggest gap: {gap}"

    accounts = getattr(job, "matched_accounts", [])
    if isinstance(accounts, (list, tuple)):
        account = next(
            (str(item).strip() for item in accounts if str(item).strip()), ""
        )
        if account:
            return f"Matched account: {account}"
    return "Evidence note: none recorded"


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


def filter_jobs(jobs: list[QueueJob], filters: BoardFilters) -> list[QueueJob]:
    view = list(jobs)
    if filters.fresh:
        view = [j for j in view if _is_apply_ready(j)]
    elif filters.status and filters.status != "all":
        view = [j for j in view if j.status == filters.status]
    if filters.autonomous:
        view = [j for j in view if not is_senior_title(j.title)]
    if filters.austin:
        view = [j for j in view if "austin" in (j.location or "").lower()]
    if filters.location:
        needle = filters.location.lower()
        view = [j for j in view if needle in (j.location or "").lower()]
    view.sort(key=_evidence_sort_key, reverse=True)
    return view[: filters.limit]


def _stats_panel(jobs: list[QueueJob]) -> Panel:
    counts = Counter(j.status for j in jobs)
    active = [j for j in jobs if j.status in _ACTIVE_QUEUE_STATUSES]
    ready = [j for j in active if _is_apply_ready(j)]
    investigate_count = len(active) - len(ready)
    loc_counts = Counter(_location_bucket(j.location) for j in ready)
    lines = [
        f"[bold]Queue[/bold]  [green]{len(ready)}[/] ready  "
        f"[yellow]{investigate_count}[/] investigate  "
        f"[yellow]{counts.get('applied', 0)}[/] applied  "
        f"[red]{counts.get('rejected', 0)}[/] rejected  "
        f"[cyan]{counts.get('submitted', 0)}[/] submitted",
    ]
    if loc_counts:
        loc_bits = "  ".join(f"{k}: {v}" for k, v in loc_counts.most_common(5))
        lines.append(f"[dim]Locations (ready):[/dim] {loc_bits}")
    return Panel(
        "\n".join(lines), title="Pipeline", border_style="blue", box=box.ROUNDED
    )


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


def _next_up_panel(jobs: list[QueueJob]) -> Panel:
    ready = [j for j in jobs if _is_apply_ready(j)]
    if not ready:
        investigate_count = sum(j.status in _ACTIVE_QUEUE_STATUSES for j in jobs)
        return Panel(
            "[yellow]No apply-ready roles.[/yellow] "
            f"{investigate_count} active role(s) need investigation. "
            "Run [cyan]jobpilot queue --refresh[/cyan] to refresh source evidence.",
            title="Next Up",
            border_style="yellow",
        )
    top = max(ready, key=_evidence_sort_key)
    materials = materials_ready(top.company)
    mat_line = (
        f"[green]Materials ready:[/green] {materials}"
        if materials
        else "[dim]No paste sheet yet — run answer draft + make_paste_sheet[/dim]"
    )
    body = (
        f"[bold cyan]{top.company}[/bold cyan] — [bold]{top.title}[/bold]\n"
        f"Decision [bold]{_readable_label(top.decision)}[/bold]  ·  "
        f"Qualification floor {_axis_text(top.qualification_lower_bound)}  ·  "
        f"Coverage {_coverage_text(top.evidence_coverage)}\n"
        f"Posting evidence [bold]{_legitimacy_text(top)}[/bold]  ·  "
        f"Work context {_axis_text(top.work_context_match)}  ·  {top.location}\n"
        f"{_evidence_note(top)}\n"
        f"[dim]{top.url}[/dim]\n"
        f"ID [bold]{top.id}[/bold]  ·  portal {top.portal}\n"
        f"{mat_line}\n"
        f'[dim]Apply:[/dim] open URL in your browser · paste from sheet · [cyan]jobpilot log {top.company} -t "{top.title}" -u "{top.url}"[/cyan]'
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
    table.add_column("Company", style="white", max_width=14)
    table.add_column("Role", style="green", max_width=26)
    table.add_column("Decision", max_width=11)
    table.add_column("Qual", justify="right", width=4)
    table.add_column("Cover", justify="right", width=5)
    table.add_column("Context", justify="right", width=7)
    table.add_column("Posting", max_width=13)
    table.add_column("Evidence", max_width=20)
    table.add_column("Status", width=10)

    for i, job in enumerate(jobs, 1):
        table.add_row(
            str(i),
            job.company,
            job.title[:26],
            _readable_label(getattr(job, "decision", "")),
            _axis_text(getattr(job, "qualification_lower_bound", None), compact=True),
            _coverage_text(getattr(job, "evidence_coverage", None)),
            _axis_text(getattr(job, "work_context_match", None), compact=True),
            _legitimacy_text(job),
            _evidence_note(job),
            Text(job.status, style=_status_style(job.status)),
        )
    return table


def _recent_table(rows: list[Any], limit: int = 6) -> Table:
    table = Table(
        title=f"Recent Applications (last {limit})", box=box.SIMPLE, expand=True
    )
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
        bits.append("apply-ready")
    else:
        bits.append(f"status={filters.status}")
    bits.append(f"top {filters.limit}")
    return " · ".join(bits)


def build_board_renderable(
    *,
    filters: BoardFilters | None = None,
    serve_port: int = DEFAULT_SERVE_PORT,
) -> RenderableType:
    """Assemble the full terminal board as a Rich renderable."""
    filters = filters or BoardFilters()
    jobs = queue_builder.load_current_slate()
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
        subtitle=f"[dim]Web: http://127.0.0.1:{serve_port}/ · CLI: jobpilot board --watch[/dim]",
        border_style="cyan",
        box=box.DOUBLE,
    )

    top_row = Columns(
        [
            _stats_panel(jobs),
            _tracker_panel(stats),
            _services_panel(
                dashboard_up=check_dashboard(serve_port),
                chrome_up=check_chrome(),
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
            '[cyan]jobpilot answer draft <co> "question"[/cyan]',
            border_style="dim",
            box=box.ROUNDED,
        )
    )
    return Group(*parts)


def render_board(
    console: Console,
    *,
    filters: BoardFilters | None = None,
    serve_port: int = DEFAULT_SERVE_PORT,
) -> None:
    """Print the terminal board once."""
    console.print(build_board_renderable(filters=filters, serve_port=serve_port))


def watch_board(
    console: Console,
    *,
    filters: BoardFilters | None = None,
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
