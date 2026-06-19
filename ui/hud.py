"""Full-screen terminal HUD — all gigs, jobs, pipeline, and next actions."""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Callable, Optional

from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from jobpilot.core.application_tracker import get_application_tracker
from jobpilot.core.config import DEFAULT_SERVE_PORT
from jobpilot.core.profile_store import get_profile_store
from jobpilot.core.queue_builder import QueueJob
from jobpilot.gigs.core.models import Gig
from jobpilot.core.work_style import is_contract_friendly, is_schedule_rigid
from jobpilot.gigs.core.scorer import apply_friction
from jobpilot.ui.hud_actions import (
    copy_text,
    draft_gig_proposal,
    open_materials,
    open_url,
    pick_with_fzf,
    selected_gig_url,
    set_gig_pipeline_status,
    skip_job,
)
from jobpilot.ui.hud_state import HudData, HudState
from jobpilot.ui.income_data import (
    IncomeViewOptions,
    gig_badges,
    gig_pay_label,
    gig_top_reason,
    load_gigs,
    load_jobs,
    load_pipeline_rows,
    pipeline_summary,
    short_url,
)
from jobpilot.ui.view_helpers import check_chrome, check_dashboard, materials_ready, score_bar
from jobpilot.ui.center_panes import _outreach_packages
from jobpilot.ui.terminal_keys import osc8_link, raw_stdin, read_key

AUSTIN_ARRIVAL = date(2026, 6, 30)
KEY_HELP = (
    "[bold]j/k[/] or [bold]↑↓[/] move  ·  [bold]Tab[/] gigs↔jobs  ·  [bold]o[/] open  ·  "
    "[bold]c[/] copy URL  ·  [bold]s[/] save gig  ·  [bold]p[/] pass job  ·  [bold]d[/] draft  ·  "
    "[bold]m[/] materials  ·  [bold]r[/] refresh  ·  [bold]?[/] help  ·  [bold]q[/] quit"
)
PLAIN_KEY_HELP = (
    "[bold]↑↓[/] move  ·  [bold]Tab[/] switch list  ·  [bold]o[/] open posting  ·  "
    "[bold]m[/] open application kit  ·  [bold]r[/] refresh  ·  [bold]q[/] quit"
)


def _friction_label(gig: Gig, *, plain: bool) -> str:
    n = apply_friction(gig)
    if not plain:
        return str(n)
    if n <= 3:
        return "Quick"
    if n <= 6:
        return "Some steps"
    return "Heavy"


def _gig_badges_plain(gig: Gig) -> str:
    text = f"{gig.title} {gig.description}"
    bits = []
    if is_contract_friendly(text, title=gig.title):
        bits.append("Contract")
    if any("async" in r for r in (gig.fit_reasons or [])):
        bits.append("Flexible")
    if is_schedule_rigid(text, title=gig.title):
        bits.append("Fixed hours")
    return ", ".join(bits) if bits else "—"


def _pipeline_status_label(status: str, *, plain: bool) -> Text:
    if not plain:
        st_style = {
            "sent": "bold green",
            "drafted": "cyan",
            "saved": "yellow",
            "new": "white",
        }.get(status, "dim")
        return Text(status, style=st_style)
    labels = {
        "new": "New lead",
        "saved": "Saved",
        "drafted": "Draft ready",
        "sent": "Sent",
        "replied": "Replied",
        "interview": "Interview",
    }
    st_style = {
        "sent": "bold green",
        "drafted": "cyan",
        "saved": "yellow",
        "new": "white",
        "replied": "bold green",
        "interview": "bold magenta",
    }.get(status, "dim")
    return Text(labels.get(status, status.replace("_", " ").title()), style=st_style)


def _austin_countdown() -> str:
    today = date.today()
    days = (AUSTIN_ARRIVAL - today).days
    if days > 0:
        return f"{days}d to Austin"
    if days == 0:
        return "Austin today"
    return f"Austin +{-days}d"


def _row_heat_style(score: int, *, selected: bool) -> str:
    score = max(0, min(100, int(score or 0)))
    base = ""
    if score >= 75:
        base = "on grey23"
    elif score >= 55:
        base = "on grey15"
    elif score >= 40:
        base = "on grey11"
    if selected:
        return f"bold reverse {base}".strip()
    return base


def _apply_text_filter_gigs(gigs: list[Gig], filt: str) -> list[Gig]:
    q = (filt or "").strip().lower()
    if not q:
        return gigs
    return [
        g for g in gigs
        if q in (g.company or "").lower()
        or q in (g.title or "").lower()
        or q in (g.source or "").lower()
    ]


def _apply_text_filter_jobs(jobs: list[QueueJob], filt: str) -> list[QueueJob]:
    q = (filt or "").strip().lower()
    if not q:
        return jobs
    return [
        j for j in jobs
        if q in j.company.lower()
        or q in j.title.lower()
        or q in (j.location or "").lower()
    ]


def _clamp_index(index: int, length: int) -> int:
    if length <= 0:
        return 0
    return max(0, min(index, length - 1))


def load_hud_data(
    opts: IncomeViewOptions,
    *,
    on_progress: Optional[Callable[[str], None]] = None,
) -> HudData:
    gigs, gigs_meta = load_gigs(opts, on_progress=on_progress)
    jobs = load_jobs(opts)
    pipe_rows = load_pipeline_rows(opts)
    return HudData(
        gigs=gigs,
        jobs=jobs,
        gigs_meta=gigs_meta,
        pipe_rows=pipe_rows,
        pipe_counts=pipeline_summary(pipe_rows),
    )


def _update_new_gigs(state: HudState, gigs: list[Gig]) -> None:
    current = {g.id for g in gigs}
    if state.previous_gig_ids:
        state.new_gig_ids = current - state.previous_gig_ids
    else:
        state.new_gig_ids = set()
    state.previous_gig_ids = current


def _header_panel(
    opts: IncomeViewOptions,
    data: HudData,
    state: HudState,
    *,
    interactive: bool,
    plain: bool = False,
) -> Panel:
    profile = get_profile_store().load()
    modes = []
    if opts.contract_first:
        modes.append("contract-first")
    if opts.drop_rigid_schedule:
        modes.append("anti-9-5")
    if opts.austin:
        modes.append("austin+remote")
    if opts.hide_senior_jobs:
        modes.append("no-senior")
    mode = " · ".join(modes)
    collected = data.gigs_meta.get("collected", 0)
    shown = data.gigs_meta.get("shown", 0)
    fresh = data.gigs_meta.get("fresh_count", collected)
    new_n = len(state.new_gig_ids)
    new_bit = f"  ·  [bold yellow]+{new_n} new[/]" if new_n else ""
    filt = f"  ·  filter [cyan]{state.text_filter}[/cyan]" if state.text_filter else ""
    status = ""
    if state.status_message and time.time() < state.status_until:
        status = f"\n[bold yellow]{state.status_message}[/bold yellow]"
    if plain:
        list_name = "Contract gigs" if state.lane == "gig" else "Full-time jobs"
        watch = " [dim]· live · use arrow keys[/dim]" if interactive else ""
        line1 = (
            f"[bold cyan]Job Search[/bold cyan]  "
            f"[white]{profile.first_name}[/white]  "
            f"[dim]{datetime.now().strftime('%a %b %d · %I:%M %p')}[/dim]  "
            f"[magenta]{_austin_countdown()}[/magenta]"
        )
        line2 = (
            f"Viewing [bold]{list_name}[/bold]{watch}  ·  "
            f"[bold]{shown}[/bold] new gigs  ·  "
            f"[bold]{len(data.jobs)}[/bold] jobs  ·  "
            f"[bold]{sum(data.pipe_counts.values())}[/bold] in progress{new_bit}{filt}"
        )
        outreach = _outreach_packages()
        if outreach:
            _, name = outreach[0]
            line2 += f"\n[bold yellow]→[/bold yellow] Send next: [green]{name}[/green]  [dim]jps 01 · jpo for all[/dim]"
        elif data.jobs:
            ready = sum(1 for j in data.jobs if materials_ready(j.company))
            if ready:
                line2 += f"\n[bold yellow]→[/bold yellow] [green]{ready}[/green] jobs have application kits ready  [dim]Tab → jobs · m to open kit[/dim]"
    else:
        lane = "[green]GIGS[/green]" if state.lane == "gig" else "[cyan]JOBS[/cyan]"
        watch = " [dim]· watch · keys below[/dim]" if interactive else ""
        line1 = (
            f"[bold cyan]JOBPILOT HUD[/bold cyan]  "
            f"[white]{profile.first_name} {profile.last_name}[/white]  "
            f"[dim]{datetime.now().strftime('%a %b %d · %H:%M:%S')}[/dim]  "
            f"[magenta]{_austin_countdown()}[/magenta]  "
            f"lane {lane}{watch}"
        )
        line2 = (
            f"[dim]{mode}[/dim]  ·  "
            f"gigs [bold]{shown}[/]/{fresh} fresh{new_bit}  ·  "
            f"jobs [bold]{len(data.jobs)}[/] queued  ·  "
            f"pipeline [bold]{sum(data.pipe_counts.values())}[/] active  ·  "
            f"[dim]floor $30/hr{filt}[/dim]"
        )
    return Panel(f"{line1}\n{line2}{status}", border_style="cyan", box=box.HEAVY)


def _gigs_table(gigs: list[Gig], state: HudState, *, plain: bool = False) -> Table:
    title = f"Contract gigs ({len(gigs)})" if plain else f"Contract gigs ({len(gigs)})"
    t = Table(
        title=title,
        box=box.SIMPLE_HEAD,
        expand=True,
        show_lines=False,
        pad_edge=False,
    )
    t.add_column("", width=1, no_wrap=True)
    t.add_column("#", width=3, justify="right", style="dim")
    if plain:
        t.add_column("Match", width=13, no_wrap=True)
        t.add_column("Company", max_width=14, no_wrap=True)
        t.add_column("Role", style="green", max_width=24, no_wrap=True)
        t.add_column("Pay", width=11, style="yellow", no_wrap=True)
        t.add_column("Apply", width=8, no_wrap=True)
        t.add_column("Type", max_width=14, no_wrap=True)
        t.add_column("Why", max_width=22, style="dim", no_wrap=True)
    else:
        t.add_column("Fit", width=13, no_wrap=True)
        t.add_column("Co", max_width=12, no_wrap=True)
        t.add_column("Title", style="green", max_width=26, no_wrap=True)
        t.add_column("Pay", width=11, style="yellow", no_wrap=True)
        t.add_column("Fl", width=3, justify="right", style="dim")
        t.add_column("Src", width=6, style="dim", no_wrap=True)
        t.add_column("Flags", width=5, no_wrap=True)
        t.add_column("Signal", max_width=20, style="dim", no_wrap=True)
    for i, g in enumerate(gigs):
        selected = state.lane == "gig" and i == state.gig_index
        row_style = _row_heat_style(g.fit_score, selected=selected)
        marker = Text("▶" if selected else " ", style="bold cyan")
        num = Text(str(i + 1), style="dim")
        role = (g.title or "")[: (24 if plain else 26)]
        if g.id in state.new_gig_ids:
            role = f"★ {role}"[: (24 if plain else 26)]
        if plain:
            t.add_row(
                marker,
                num,
                score_bar(g.fit_score, width=8),
                (g.company or "—")[:14],
                role,
                gig_pay_label(g),
                _friction_label(g, plain=True),
                _gig_badges_plain(g)[:14],
                gig_top_reason(g)[:22],
                style=row_style or None,
            )
        else:
            t.add_row(
                marker,
                num,
                score_bar(g.fit_score, width=8),
                (g.company or "—")[:12],
                role,
                gig_pay_label(g),
                str(apply_friction(g)),
                (g.source or "")[:6],
                gig_badges(g),
                gig_top_reason(g)[:20],
                style=row_style or None,
            )
    return t


def _jobs_table(jobs: list[QueueJob], state: HudState, *, plain: bool = False) -> Table:
    t = Table(
        title=f"Full-time & backup jobs ({len(jobs)})" if plain else f"ATS backup ({len(jobs)})",
        box=box.SIMPLE_HEAD,
        expand=True,
        show_lines=False,
        pad_edge=False,
    )
    t.add_column("", width=1, no_wrap=True)
    t.add_column("#", width=3, justify="right", style="dim")
    if plain:
        t.add_column("Match", width=13, no_wrap=True)
        t.add_column("Company", max_width=12, no_wrap=True)
        t.add_column("Role", style="green", max_width=22, no_wrap=True)
        t.add_column("Location", max_width=14, style="magenta", no_wrap=True)
        t.add_column("Culture", width=7, justify="right")
        t.add_column("Kit", width=6, justify="center")
    else:
        t.add_column("Fit", width=13, no_wrap=True)
        t.add_column("Co", max_width=11, no_wrap=True)
        t.add_column("Title", style="green", max_width=22, no_wrap=True)
        t.add_column("Loc", max_width=14, style="magenta", no_wrap=True)
        t.add_column("Psy", width=3, justify="right")
        t.add_column("Mat", width=3, justify="center")
        t.add_column("ID", width=8, style="dim", no_wrap=True)
    for i, j in enumerate(jobs):
        selected = state.lane == "job" and i == state.job_index
        row_style = _row_heat_style(j.fit_score, selected=selected)
        marker = Text("▶" if selected else " ", style="bold cyan")
        if plain:
            kit = "Ready" if materials_ready(j.company) else "—"
            culture = f"{j.psyche_score}/15"
            t.add_row(
                marker,
                str(i + 1),
                score_bar(j.fit_score, width=8),
                j.company[:12],
                j.title[:22],
                (j.location or "—")[:14],
                culture,
                kit,
                style=row_style or None,
            )
        else:
            mat = "✓" if materials_ready(j.company) else "·"
            t.add_row(
                marker,
                str(i + 1),
                score_bar(j.fit_score, width=8),
                j.company[:11],
                j.title[:22],
                (j.location or "—")[:14],
                str(j.psyche_score),
                mat,
                j.id,
                style=row_style or None,
            )
    return t


def _pipeline_table(rows, *, plain: bool = False) -> Table:
    t = Table(
        title=f"Applications in progress ({len(rows)})" if plain else f"Pipeline ({len(rows)})",
        box=box.SIMPLE_HEAD,
        expand=True,
        show_lines=False,
    )
    if plain:
        t.add_column("Stage", width=12)
        t.add_column("Match", width=4, justify="right")
        t.add_column("Company", max_width=14)
        t.add_column("Role", style="green", max_width=24)
        t.add_column("Pay", width=12, style="yellow")
        t.add_column("Next step", max_width=18, style="dim")
    else:
        t.add_column("St", width=8)
        t.add_column("Sc", width=4, justify="right")
        t.add_column("Company", max_width=14)
        t.add_column("Role", style="green", max_width=26)
        t.add_column("Pay", width=12, style="yellow")
        t.add_column("Next", max_width=16, style="dim")
    for r in rows:
        t.add_row(
            _pipeline_status_label(r.status, plain=plain),
            str(r.score or ""),
            (r.company or "")[:14],
            (r.role or "")[: (24 if plain else 26)],
            (r.pay or "?")[:12],
            (r.next_action or "")[: (18 if plain else 16)],
        )
    return t


def _score_anatomy_gig(gig: Gig) -> str:
    lines = [f"[bold]Fit {gig.fit_score}[/bold] · friction {apply_friction(gig)}"]
    for r in (gig.fit_reasons or [])[:6]:
        lines.append(f"  [dim]·[/dim] {r}")
    if not gig.fit_reasons:
        lines.append("  [dim]no scorer reasons[/dim]")
    return "\n".join(lines)


def _score_anatomy_job(job: QueueJob) -> str:
    lines = [
        f"[bold]Fit {job.fit_score}[/bold] · psyche {job.psyche_score}/15 · track {job.track}",
    ]
    if job.keywords:
        lines.append(f"  [dim]keywords:[/dim] {', '.join(job.keywords[:8])}")
    return "\n".join(lines)


def _detail_panel(
    gigs: list[Gig],
    jobs: list[QueueJob],
    state: HudState,
    *,
    plain: bool = False,
) -> Panel:
    lines: list[str] = []
    if state.lane == "gig" and gigs:
        idx = _clamp_index(state.gig_index, len(gigs))
        g = gigs[idx]
        url = selected_gig_url(g)
        link = osc8_link(url, "Open posting" if plain else short_url(url, 64)) if url else "[dim]no link[/dim]"
        if plain:
            lines.append(
                f"[bold green]{g.company}[/bold green] — {g.title}\n"
                f"  {gig_pay_label(g)} · {_gig_badges_plain(g)} · {_friction_label(g, plain=True)} apply\n"
                f"  {link}"
            )
        else:
            lines.append(
                f"[bold green]G{idx + 1}[/bold green] {g.company} — {g.title}\n"
                f"  {gig_pay_label(g)} · {gig_badges(g)}\n"
                f"  [dim]apply[/dim] {link}\n"
                f"{_score_anatomy_gig(g)}"
            )
    elif state.lane == "job" and jobs:
        idx = _clamp_index(state.job_index, len(jobs))
        j = jobs[idx]
        if plain:
            mat = "Application kit ready" if materials_ready(j.company) else "No kit yet"
            link = osc8_link(j.url, "Open posting")
            lines.append(
                f"[bold cyan]{j.company}[/bold cyan] — {j.title}\n"
                f"  Match {j.fit_score}/100 · {mat} · {j.location}\n"
                f"  {link}"
            )
        else:
            mat = "paste sheet ready" if materials_ready(j.company) else "no materials yet"
            link = osc8_link(j.url, short_url(j.url, 64))
            lines.append(
                f"[bold cyan]J{idx + 1}[/bold cyan] {j.company} — {j.title}\n"
                f"  {score_bar(j.fit_score, width=8)} · {mat} · {j.location}\n"
                f"  [dim]url[/dim] {link}\n"
                f"{_score_anatomy_job(j)}"
            )
    elif gigs:
        g = gigs[0]
        url = selected_gig_url(g)
        link = osc8_link(url, short_url(url, 64)) if url else "[dim]no url[/dim]"
        lines.append(
            f"[bold green]G1[/bold green] {g.company} — {g.title}\n"
            f"  {gig_pay_label(g)} · friction {apply_friction(g)}\n"
            f"  [dim]apply[/dim] {link}"
        )
    elif jobs:
        j = jobs[0]
        mat = "paste sheet ready" if materials_ready(j.company) else "no materials yet"
        link = osc8_link(j.url, short_url(j.url, 64))
        lines.append(
            f"[bold cyan]J1[/bold cyan] {j.company} — {j.title}\n"
            f"  {score_bar(j.fit_score, width=8)} psyche {j.psyche_score} · {mat}\n"
            f"  [dim]url[/dim] {link}"
        )
    else:
        lines.append(
            "[yellow]No leads yet — try refreshing your queue[/yellow]"
            if plain
            else "[yellow]No leads — run `jobpilot gigs digest --contract-first`[/yellow]"
        )
    title = "Selected opportunity" if plain else "Selection · score anatomy"
    return Panel("\n\n".join(lines), title=title, border_style="green", box=box.ROUNDED)


def _feed_panel(state: HudState) -> Panel:
    body = "\n".join(state.feed_lines[-12:]) if state.feed_lines else "[dim]Scan idle[/dim]"
    return Panel(body, title="Scan feed", border_style="dim", box=box.ROUNDED)


def _url_index_panel(gigs, jobs, *, limit: int = 40) -> Panel:
    lines = []
    for i, g in enumerate(gigs[:limit], 1):
        url = selected_gig_url(g)
        lines.append(f"[green]G{i:02d}[/green] [dim]{short_url(url, 72)}[/dim]")
    for i, j in enumerate(jobs[:limit], 1):
        lines.append(f"[cyan]J{i:02d}[/cyan] [dim]{short_url(j.url, 72)}[/dim]")
    body = "\n".join(lines) if lines else "[dim]No URLs[/dim]"
    return Panel(body, title="URL index (G=gig · J=job)", border_style="dim", box=box.ROUNDED)


def _help_panel(*, plain: bool = False) -> Panel:
    return Panel(PLAIN_KEY_HELP if plain else KEY_HELP, title="Controls", border_style="yellow", box=box.ROUNDED)


def _footer_panel(port: int = DEFAULT_SERVE_PORT, *, interactive: bool = False, plain: bool = False) -> Panel:
    dash_ok = check_dashboard(port)
    chrome_ok = check_chrome()
    tracker = get_application_tracker()
    try:
        stats = tracker.get_stats()
        submitted = stats.get("submitted", 0)
        total = stats.get("total", 0)
    finally:
        tracker.close()
    if plain:
        dash = "[green]running[/green]" if dash_ok else "[red]stopped[/red]"
        chrome = "[green]ready[/green]" if chrome_ok else "[red]not ready[/red]"
        keys = f"\n{PLAIN_KEY_HELP}" if interactive else ""
        body = (
            f"Dashboard {dash}  ·  Browser helper {chrome}  ·  "
            f"{submitted} submitted / {total} tracked{keys}"
        )
    else:
        dash = "up" if dash_ok else "down"
        chrome = "up" if chrome_ok else "down"
        tr = f"{total} tracked · {submitted} submitted"
        keys = f"\n{KEY_HELP}" if interactive else (
            f"\n[dim]Ctrl+C exit ·[/dim] "
            f"[cyan]jobpilot hud --watch[/cyan] for keyboard control"
        )
        body = (
            f"[dim]dash:{dash}[/dim] :{port}  [dim]chrome:{chrome}[/dim] :9222  ·  {tr}\n"
            f"[dim]Flags:[/dim] [green]C[/]=contract [cyan]A[/]=async [red]9-5[/]=rigid · "
            f"Fl=friction · Mat ✓=paste sheet · ★=new since refresh{keys}"
        )
    return Panel(body, border_style="dim", box=box.ROUNDED)


def build_hud_layout(
    opts: Optional[IncomeViewOptions] = None,
    *,
    state: Optional[HudState] = None,
    data: Optional[HudData] = None,
    verbose: bool = False,
    interactive: bool = False,
    plain: bool = False,
) -> Layout:
    opts = opts or IncomeViewOptions()
    state = state or HudState()
    if data is None:
        data = load_hud_data(opts)

    gigs = _apply_text_filter_gigs(data.gigs, state.text_filter)
    jobs = _apply_text_filter_jobs(data.jobs, state.text_filter)
    state.gig_index = _clamp_index(state.gig_index, len(gigs))
    state.job_index = _clamp_index(state.job_index, len(jobs))

    main = Layout(name="main")
    main.split_row(
        Layout(Panel(_gigs_table(gigs, state, plain=plain), border_style="green"), name="gigs", ratio=3),
        Layout(Panel(_jobs_table(jobs, state, plain=plain), border_style="blue"), name="jobs", ratio=2),
    )

    sections: list[Layout] = [
        Layout(
            _header_panel(opts, data, state, interactive=interactive, plain=plain),
            name="header",
            size=5 if state.status_message else 4,
        ),
        main,
    ]
    if data.pipe_rows:
        pipe_h = min(14, max(5, 3 + len(data.pipe_rows)))
        sections.append(
            Layout(
                Panel(_pipeline_table(data.pipe_rows, plain=plain), border_style="yellow"),
                name="pipe",
                size=pipe_h,
            )
        )
    if interactive and state.feed_lines and not plain:
        sections.append(Layout(_feed_panel(state), name="feed", size=min(8, 3 + len(state.feed_lines))))
    if state.show_help:
        sections.append(Layout(_help_panel(plain=plain), name="help", size=4))
    sections.append(Layout(_detail_panel(gigs, jobs, state, plain=plain), name="detail", size=9))
    if verbose and not plain:
        url_h = min(18, max(6, 2 + len(gigs) + len(jobs)))
        sections.append(Layout(_url_index_panel(gigs, jobs), name="urls", size=url_h))
    sections.append(
        Layout(_footer_panel(interactive=interactive, plain=plain), name="footer", size=6 if interactive else 5)
    )

    root = Layout(name="root")
    root.split_column(*sections)
    return root


def _flash(state: HudState, message: str, *, seconds: float = 4.0) -> None:
    state.flash(message, until=time.time() + seconds)


def _selected_gig(gigs: list[Gig], state: HudState) -> Optional[Gig]:
    if not gigs:
        return None
    return gigs[_clamp_index(state.gig_index, len(gigs))]


def _selected_job(jobs: list[QueueJob], state: HudState) -> Optional[QueueJob]:
    if not jobs:
        return None
    return jobs[_clamp_index(state.job_index, len(jobs))]


def _reload_data(
    opts: IncomeViewOptions,
    state: HudState,
) -> HudData:
    state.feed_lines.clear()
    data = load_hud_data(
        opts,
        on_progress=lambda msg: state.feed_lines.append(msg),
    )
    _update_new_gigs(state, data.gigs)
    return data


def _handle_key(
    key: str,
    state: HudState,
    data: HudData,
    opts: IncomeViewOptions,
    *,
    gigs: list[Gig],
    jobs: list[QueueJob],
) -> tuple[bool, Optional[str]]:
    """Return (continue_loop, pending_prompt). pending_prompt is 'pass' for job skip."""
    if key in ("q", "esc"):
        return False, None
    if key == "?":
        state.show_help = not state.show_help
        return True, None
    if key in ("\t",):
        state.toggle_lane()
        return True, None
    if key in ("j", "down"):
        if state.lane == "gig" and gigs:
            state.gig_index = _clamp_index(state.gig_index + 1, len(gigs))
        elif state.lane == "job" and jobs:
            state.job_index = _clamp_index(state.job_index + 1, len(jobs))
        return True, None
    if key in ("k", "up"):
        if state.lane == "gig" and gigs:
            state.gig_index = _clamp_index(state.gig_index - 1, len(gigs))
        elif state.lane == "job" and jobs:
            state.job_index = _clamp_index(state.job_index - 1, len(jobs))
        return True, None
    if key == "o":
        if state.lane == "gig":
            g = _selected_gig(gigs, state)
            if g and open_url(selected_gig_url(g)):
                _flash(state, f"opened {g.company}")
            else:
                _flash(state, "no URL")
        else:
            j = _selected_job(jobs, state)
            if j and open_url(j.url):
                _flash(state, f"opened {j.company}")
            else:
                _flash(state, "no URL")
        return True, None
    if key == "c":
        if state.lane == "gig":
            g = _selected_gig(gigs, state)
            url = selected_gig_url(g) if g else ""
        else:
            j = _selected_job(jobs, state)
            url = j.url if j else ""
        if copy_text(url):
            _flash(state, "copied URL")
        else:
            _flash(state, "copy failed")
        return True, None
    if key == "s" and state.lane == "gig":
        g = _selected_gig(gigs, state)
        if g:
            _flash(state, set_gig_pipeline_status(g, "saved"))
        return True, None
    if key == "p" and state.lane == "job":
        return True, "pass"
    if key == "d" and state.lane == "gig":
        g = _selected_gig(gigs, state)
        if g:
            _flash(state, draft_gig_proposal(g))
        return True, None
    if key == "m" and state.lane == "job":
        j = _selected_job(jobs, state)
        if j:
            _flash(state, open_materials(j.company))
        return True, None
    if key == "r":
        return True, "refresh"
    if key == "/":
        return True, "filter"
    return True, None


def _lock_terminal_for_hud(console: Console) -> None:
    """Use alternate screen + clear scrollback so wheel/trackpad doesn't expose old logs."""
    import sys

    try:
        sys.stdout.write("\033]1337;ClearScrollback\033\\")
        sys.stdout.flush()
    except Exception:
        pass
    try:
        console.clear()
        console.set_alt_screen(True)
    except Exception:
        pass


def watch_hud(
    console: Console,
    *,
    opts: Optional[IncomeViewOptions] = None,
    interval: float = 30.0,
    verbose: bool = False,
    plain: bool = False,
) -> None:
    from rich.live import Live

    opts = opts or IncomeViewOptions()
    _lock_terminal_for_hud(console)
    state = HudState()
    data = _reload_data(opts, state)
    running = True
    last_refresh = time.time()

    def _filtered():
        gigs = _apply_text_filter_gigs(data.gigs, state.text_filter)
        jobs = _apply_text_filter_jobs(data.jobs, state.text_filter)
        return gigs, jobs

    with Live(console=console, refresh_per_second=8, screen=True) as live:
        while running:
            gigs, jobs = _filtered()
            live.update(
                build_hud_layout(
                    opts,
                    state=state,
                    data=data,
                    verbose=verbose,
                    interactive=True,
                    plain=plain,
                )
            )

            pending: Optional[str] = None
            with raw_stdin():
                deadline = last_refresh + interval
                while running and pending is None:
                    timeout = max(0.05, min(0.2, deadline - time.time()))
                    key = read_key(timeout=timeout)
                    if key:
                        running, pending = _handle_key(
                            key, state, data, opts, gigs=gigs, jobs=jobs,
                        )
                    elif time.time() >= deadline:
                        pending = "refresh"
                        break

            if not running:
                break

            if pending == "refresh":
                data = _reload_data(opts, state)
                last_refresh = time.time()
                continue

            if pending == "filter":
                filt = Prompt.ask("Filter (company/title)", default=state.text_filter)
                state.text_filter = filt.strip()
                state.gig_index = 0
                state.job_index = 0
                _flash(state, f"filter: {state.text_filter or '(cleared)'}")
                continue

            if pending == "pass":
                j = _selected_job(jobs, state)
                if j:
                    reason = Prompt.ask("Pass reason (optional)", default="")
                    msg = skip_job(j)
                    if reason:
                        msg = f"{msg} — {reason}"
                    _flash(state, msg)
                continue


def pick_hud(
    console: Console,
    *,
    opts: Optional[IncomeViewOptions] = None,
) -> None:
    opts = opts or IncomeViewOptions()
    data = load_hud_data(opts)
    lines: list[str] = []
    urls: list[str] = []
    for g in data.gigs:
        lines.append(f"[G {g.fit_score:3d}] {g.company} — {g.title}")
        urls.append(selected_gig_url(g))
    for j in data.jobs:
        lines.append(f"[J {j.fit_score:3d}] {j.company} — {j.title}")
        urls.append(j.url)
    idx = pick_with_fzf(lines)
    if idx is None:
        console.print("[dim]pick cancelled[/dim]")
        return
    url = urls[idx]
    if open_url(url):
        console.print(f"[green]opened[/green] {short_url(url, 80)}")
    else:
        console.print("[yellow]no URL[/yellow]")


def render_hud(
    console: Console,
    *,
    opts: Optional[IncomeViewOptions] = None,
    verbose: bool = False,
    plain: bool = False,
) -> None:
    console.print(build_hud_layout(opts=opts, verbose=verbose, plain=plain))


def export_hud_text(opts: Optional[IncomeViewOptions] = None) -> str:
    """Plain-text export of all HUD rows (for piping / logs)."""
    opts = opts or IncomeViewOptions()
    data = load_hud_data(opts)
    gigs, jobs = data.gigs, data.jobs
    lines = ["=== GIGS ==="]
    for i, g in enumerate(gigs, 1):
        lines.append(
            f"{i:02d} | {g.fit_score:3d} | {g.company} | {g.title} | {gig_pay_label(g)} | "
            f"friction={apply_friction(g)} | {g.apply_url or g.url}"
        )
    lines.append("=== JOBS ===")
    for i, j in enumerate(jobs, 1):
        lines.append(
            f"{i:02d} | {j.fit_score:3d} | {j.company} | {j.title} | {j.location} | "
            f"id={j.id} | {j.url}"
        )
    return "\n".join(lines)