"""Unified autonomous-income radar — gigs + jobs in one terminal view."""

from __future__ import annotations

import re
from typing import Optional

from rich import box
from rich.columns import Columns
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table

from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.scorer import apply_friction
from jobpilot.ui.income_data import IncomeViewOptions, gig_pay_label, load_gigs, load_jobs
from jobpilot.ui.view_helpers import materials_ready, score_bar

# Backward-compatible alias — radar now shares IncomeViewOptions with HUD.
RadarOptions = IncomeViewOptions


def _gigs_table(gigs: list[Gig]) -> Table:
    table = Table(title="Contract lane (gigs)", box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("#", width=3, justify="right", style="dim")
    table.add_column("Fit", width=14)
    table.add_column("Company", max_width=14)
    table.add_column("Title", style="green", max_width=32)
    table.add_column("Pay", width=12, style="yellow")
    table.add_column("Friction", width=8, justify="right")
    table.add_column("Src", width=8, style="dim")
    for i, g in enumerate(gigs, 1):
        table.add_row(
            str(i),
            score_bar(g.fit_score),
            (g.company or "—")[:14],
            (g.title or "")[:32],
            gig_pay_label(g),
            str(apply_friction(g)),
            g.source,
        )
    return table


def _jobs_table(jobs) -> Table:
    table = Table(title="Jobs lane backup (queued ATS)", box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("#", width=3, justify="right", style="dim")
    table.add_column("Fit", width=14)
    table.add_column("Company", max_width=14)
    table.add_column("Title", style="green", max_width=30)
    table.add_column("Location", max_width=16, style="magenta")
    table.add_column("ID", width=8, style="dim")
    table.add_column("📋", width=3, justify="center")
    for i, j in enumerate(jobs, 1):
        ready = "✓" if materials_ready(j.company) else "·"
        table.add_row(
            str(i),
            score_bar(j.fit_score),
            j.company[:14],
            j.title[:30],
            (j.location or "—")[:16],
            j.id,
            ready,
        )
    return table


def _income_velocity_panel() -> Panel:
    try:
        from jobpilot.gigs.core import pipeline

        rows = pipeline.parse()
    except Exception:
        return Panel("[dim]Pipeline not initialized — run `jobpilot gigs digest`[/dim]", title="Income velocity")

    active = [r for r in rows if r.status in {"saved", "drafted", "sent", "replied", "interview"}]
    sent = sum(1 for r in rows if r.status in {"sent", "replied", "interview"})
    drafted = sum(1 for r in rows if r.status == "drafted")
    potential_hr = 0.0
    for row in active:
        pay = (row.pay or "").lower()
        if "/hr" in pay or "hr" in pay:
            m = re.search(r"\$?(\d+)", pay)
            if m:
                potential_hr = max(potential_hr, float(m.group(1)))
    est_week = f"~${potential_hr * 20:,.0f}/wk potential" if potential_hr >= 30 else "pay bands unclear on active rows"
    return Panel(
        f"[bold]{len(active)}[/] active pipeline rows  "
        f"[cyan]{drafted}[/] drafted  [green]{sent}[/] sent  "
        f"[dim]{est_week}[/dim]",
        title="Income velocity",
        border_style="yellow",
    )


def _mode_caption(opts: IncomeViewOptions) -> str:
    bits = []
    if opts.contract_first:
        bits.append("contract-first")
    if opts.drop_rigid_schedule:
        bits.append("anti-9-5")
    if opts.austin:
        bits.append("austin+remote")
    if opts.hide_senior_jobs:
        bits.append("no-senior")
    return " · ".join(bits) or "default"


def build_radar_renderable(opts: Optional[IncomeViewOptions] = None) -> RenderableType:
    opts = opts or IncomeViewOptions(gigs_limit=8, jobs_limit=8)
    gigs, _meta = load_gigs(opts)
    jobs = load_jobs(opts)

    header = Panel(
        f"[bold cyan]Autonomous Income Radar[/bold cyan]\n"
        f"[dim]{_mode_caption(opts)} · gigs ≥{opts.min_gig_score} · June 30 Austin return[/dim]\n"
        f"[dim]Primary: contract gigs · Backup: non-senior queued ATS[/dim]",
        border_style="cyan",
        box=box.DOUBLE,
    )

    parts: list[RenderableType] = [
        header,
        Columns([_income_velocity_panel()], expand=True),
    ]

    if gigs:
        parts.append(_gigs_table(gigs))
        top = gigs[0]
        parts.append(Panel(
            f"[bold]{top.company}[/bold] — {top.title}\n"
            f"{score_bar(top.fit_score)}  {gig_pay_label(top)}  friction {apply_friction(top)}\n"
            f"[dim]{top.apply_url or top.url}[/dim]\n"
            f"[dim]Next:[/dim] `jobpilot gigs digest` to push · mark [cyan]s[/cyan] in pipeline on phone",
            title="Top contract lead",
            border_style="green",
        ))
    else:
        parts.append(Panel(
            "[yellow]No contract gigs matched.[/yellow] Try `--min-gig-score 35` or `--no-contract-first`.",
            title="Contract lane",
        ))

    if jobs:
        parts.append(_jobs_table(jobs))
    else:
        parts.append(Panel(
            "[dim]No queued backup jobs for this filter.[/dim]",
            title="Jobs lane",
        ))

    parts.append(Panel(
        "[dim]Commands:[/dim] "
        "[cyan]jobpilot hud --watch[/cyan] · "
        "[cyan]jobpilot radar --watch[/cyan] · "
        "[cyan]jobpilot gigs digest --contract-first[/cyan] · "
        "[cyan]jobpilot board --austin[/cyan]",
        border_style="dim",
    ))
    return Group(*parts)


def render_radar(console: Console, *, opts: Optional[IncomeViewOptions] = None) -> None:
    console.print(build_radar_renderable(opts=opts))


def watch_radar(console: Console, *, opts: Optional[IncomeViewOptions] = None, interval: float = 30.0) -> None:
    import time
    from rich.live import Live

    with Live(console=console, refresh_per_second=2, screen=True) as live:
        try:
            while True:
                live.update(build_radar_renderable(opts=opts))
                time.sleep(interval)
        except KeyboardInterrupt:
            pass