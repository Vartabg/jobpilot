"""
Gigpilot CLI — daily digest of fresh tech gigs.

    gigpilot scan        # show top gigs from all sources (dry run, no dispatch)
    gigpilot digest      # scan + filter + dedupe + dispatch to iCloud + ntfy
    gigpilot stats       # dedupe + pipeline + health summary
    gigpilot health      # source health dashboard + last digest heartbeat
    gigpilot feedback    # aggregate pass reasons from pipeline Notes

Designed to be called from a launchd scheduled job (default: 8am + 5pm).
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from jobpilot.gigs.core.collect import collect_all, include_upwork_exports
from jobpilot.gigs.core.dedupe import dedupe_cross_source, dedupe_cross_source_with_groups
from jobpilot.gigs.core import feedback, pipeline, run_state, source_health
from jobpilot.gigs.core.dispatcher import dispatch, push_failure
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.pipeline_migrate import migrate_applied_into_pipeline
from jobpilot.gigs.core.preferences import write_default_if_missing as _ensure_prefs
from jobpilot.gigs.core.scorer import filter_and_rank
from jobpilot.gigs.core.scrapers.weworkremotely import enrich_apply_urls as enrich_wwr
from jobpilot.gigs.core.store import filter_new, mark_archived, mark_seen, seen_count, sync_first_seen
from jobpilot.gigs.core.away import sync_reminders_from_pipeline

app = typer.Typer(help="Daily tech-gig digest")
console = Console()


@app.callback()
def _gigs_setup() -> None:
    """Seed prefs and apply any scoring overrides on first use of a gigs command.

    Done here, not at import, so merely importing this module (e.g. when the
    parent `jobpilot` CLI mounts the gigs sub-app for `--help`) never touches
    the filesystem.
    """
    from jobpilot.gigs.core import scoring_rules

    _ensure_prefs()
    scoring_rules.apply_overrides()


def _collect_with_status() -> list[Gig]:
    if not include_upwork_exports():
        console.print(
            "[dim]→ Upwork exports skipped (set GIGPILOT_INCLUDE_UPWORK=1 to enable)[/dim]",
        )
    gigs, results = collect_all()
    for result in results:
        if result.ok:
            console.print(f"  [green]✓[/green] {result.name}: {result.fetched} gigs")
        else:
            console.print(f"  [yellow]⚠[/yellow] {result.name} failed: {result.error}")
    return gigs


@app.command()
def scan(
    min_score: int = typer.Option(55, "--min-score", help="Minimum fit score to show"),
    top_n: int = typer.Option(15, "--top", help="How many top gigs to show"),
    all: bool = typer.Option(False, "--all", help="Show all gigs regardless of 'seen' state"),
    contract_first: bool = typer.Option(False, "--contract-first", help="Prefer contract/1099/hourly; drop explicit W-2-only"),
    anti_schedule: bool = typer.Option(False, "--anti-schedule", help="Drop postings with 9-5 / core-hours language"),
):
    """Scan all sources and show the top gigs. Doesn't send anything."""
    all_gigs = _collect_with_status()
    console.print(f"\n[bold]{len(all_gigs)}[/bold] total gigs collected")

    if not all:
        new_ids = set(filter_new([g.id for g in all_gigs]))
        before = len(all_gigs)
        all_gigs = [g for g in all_gigs if g.id in new_ids]
        console.print(f"[dim]{before - len(all_gigs)} already-seen gigs filtered[/dim]")

    all_gigs, deduped = dedupe_cross_source(all_gigs)
    if deduped:
        console.print(f"[dim]{deduped} cross-source duplicates collapsed[/dim]")

    ranked = filter_and_rank(
        all_gigs,
        min_score=min_score,
        top_n=top_n,
        contract_first=contract_first,
        drop_rigid_schedule=anti_schedule,
    )
    filters = []
    if contract_first:
        filters.append("contract-first")
    if anti_schedule:
        filters.append("anti-9-5")
    filter_note = f" ({', '.join(filters)})" if filters else ""
    console.print(f"\n[bold]Top {len(ranked)} gigs (min score {min_score}){filter_note}:[/bold]\n")

    if not ranked:
        console.print("[yellow]Nothing met the bar. Try --min-score 40 or --all.[/yellow]")
        return

    t = Table(show_lines=False)
    t.add_column("Fit", justify="right", style="cyan", width=5)
    t.add_column("Company", style="white", max_width=18)
    t.add_column("Title", style="green", max_width=45)
    t.add_column("Pay", style="yellow", width=16)
    t.add_column("Src", style="dim", width=10)
    for g in ranked:
        pay = ""
        if g.salary_max:
            pay = f"${g.salary_min/1000:.0f}-${g.salary_max/1000:.0f}K"
        elif g.pay_hourly_est:
            pay = f"${g.pay_hourly_est:.0f}/hr"
        t.add_row(str(g.fit_score), g.company or "-", g.title, pay or "?", g.source)
    console.print(t)


@app.command()
def digest(
    min_score: int = typer.Option(55, "--min-score"),
    top_n: int = typer.Option(12, "--top"),
    contract_first: bool = typer.Option(False, "--contract-first", help="Prefer contract/1099/hourly postings"),
    anti_schedule: bool = typer.Option(False, "--anti-schedule", help="Drop rigid 9-5 / core-hours postings"),
):
    """Run scan → filter via pipeline + seen → rank → write pipeline + push.

    Any exception records an ok=False heartbeat and pushes a failure alert
    to the phone before re-raising — a crashed unattended run must be loud,
    not a silent gap in the morning digest.
    """
    try:
        _run_digest(
            min_score=min_score,
            top_n=top_n,
            contract_first=contract_first,
            drop_rigid_schedule=anti_schedule,
        )
    except Exception as exc:
        short = f"{type(exc).__name__}: {exc}"
        try:
            run_state.record_digest(ok=False, error=short[:500])
        except Exception as record_exc:  # never let bookkeeping eat the alert
            console.print(f"[red]record_digest failed: {record_exc}[/red]")
        push_failure(f"GigPilot digest FAILED: {short[:200]}")
        raise


def _run_digest(
    *,
    min_score: int,
    top_n: int,
    contract_first: bool = False,
    drop_rigid_schedule: bool = False,
) -> None:
    migrate_applied_into_pipeline()
    hygiene = pipeline.migrate_pipeline_hygiene()
    if hygiene["collapsed"] or hygiene["ids_regenerated"]:
        console.print(
            f"[dim]Hygiene migration: {hygiene['collapsed']} duplicate rows "
            f"archived, {hygiene['ids_regenerated']} legacy IDs regenerated[/dim]",
        )
    existing_pipeline = pipeline.parse()
    # Stamp Saved / Last touched on rows whose status the user edited since
    # the previous run (diffed against the end-of-run status snapshot).
    existing_pipeline = pipeline.stamp_status_changes(
        pipeline.load_status_snapshot(), existing_pipeline,
    )
    decided_ids = pipeline.excluded_ids(existing_pipeline)

    all_gigs = _collect_with_status()
    collected = len(all_gigs)
    all_gigs = [g for g in all_gigs if g.id not in decided_ids]
    if collected != len(all_gigs):
        console.print(
            f"[dim]{collected - len(all_gigs)} pipeline-decided gigs filtered[/dim]",
        )

    new_ids = set(filter_new([g.id for g in all_gigs]))
    fresh = [g for g in all_gigs if g.id in new_ids]
    fresh, deduped, dupe_groups = dedupe_cross_source_with_groups(fresh)
    if deduped:
        console.print(f"[dim]{deduped} cross-source duplicates collapsed[/dim]")

    ranked = filter_and_rank(
        fresh,
        min_score=min_score,
        top_n=top_n,
        contract_first=contract_first,
        drop_rigid_schedule=drop_rigid_schedule,
    )
    ranked = enrich_wwr(ranked)

    existing_ids = {r.gig_id for r in existing_pipeline if r.gig_id}
    updated = pipeline.merge_new_gigs(existing_pipeline, ranked)
    # Reposts (same company+role under a fresh ID) are skipped by the merge —
    # only gigs that actually landed as new rows get pushed to the phone.
    added_ids = {r.gig_id for r in updated if r.gig_id} - existing_ids
    added = [g for g in ranked if g.id in added_ids]
    if len(added) != len(ranked):
        console.print(
            f"[dim]{len(ranked) - len(added)} ranked gigs matched existing "
            f"pipeline rows (reposts) — not re-added[/dim]",
        )

    rescored = pipeline.rescore_new_rows(updated, all_gigs)
    if rescored:
        console.print(
            f"[dim]{rescored} still-new rows re-scored with current calibration[/dim]",
        )

    # Auto-archive: `new` rows older than ARCHIVE_AFTER_DAYS move to the
    # archive sidecar; their IDs are retired in seen.json so they never
    # resurface. sync_first_seen stamps undated rows so the clock starts now.
    first_seen = sync_first_seen(
        [r.gig_id for r in updated if r.status == "new" and r.gig_id],
    )
    updated, stale_rows = pipeline.split_archivable(updated, first_seen)
    archived_ids: set[str] = set()
    if stale_rows:
        pipeline.append_to_archive(stale_rows)
        archived_ids = {r.gig_id for r in stale_rows if r.gig_id}
        mark_archived(sorted(archived_ids))
        console.print(
            f"[dim]{len(stale_rows)} stale `new` rows "
            f"(>{pipeline.ARCHIVE_AFTER_DAYS}d) auto-archived → "
            f"{pipeline.ARCHIVE_PATH.name}[/dim]",
        )

    write_result = pipeline.write(updated, removed_ids=archived_ids)
    if write_result.refused:
        # The shrink guard kept the on-disk file. Nothing from this run was
        # persisted, so nothing may be marked seen or snapshotted — otherwise
        # these gigs are seen-but-never-written and can never resurface.
        # Raising routes through the digest() wrapper: ok=False heartbeat +
        # phone push.
        raise RuntimeError(
            "pipeline write refused by shrink guard — run not persisted "
            f"({write_result.path})"
        )
    pipeline_path = write_result.path
    console.print(f"  Pipeline: {pipeline_path}")

    added_feedback = feedback.sync_from_pipeline(updated)
    if added_feedback:
        console.print(f"  Feedback: {added_feedback} new pass reason(s) recorded")

    reminders_created = sync_reminders_from_pipeline(updated)
    if reminders_created:
        console.print(f"  Reminders: {reminders_created} new")

    # Snapshot statuses so the next run can detect user edits made between
    # runs (deliberately the statuses *we* acknowledged this run: an edit
    # landing mid-run still diffs as changed next time and gets stamped).
    pipeline.save_status_snapshot(updated)

    stale_sources = source_health.stale_zero_sources()
    failed = source_health.failed_sources()
    if stale_sources:
        console.print(
            f"[yellow]Source alert: zero results {source_health.ZERO_ALERT_STREAK}+ "
            f"runs — {', '.join(stale_sources)}[/yellow]",
        )
    for name, err in failed:
        console.print(f"[yellow]{name} last failed: {err[:80]}[/yellow]")
    source_warning = source_health.warning_line(stale=stale_sources, failed=failed)

    # Mark every ranked gig seen — and every collapsed duplicate variant
    # behind it — so variants can't resurface as `new` next run. Reposts
    # that merely matched an existing pipeline row count as seen too.
    if ranked:
        mark_seen(sorted({m for g in ranked for m in dupe_groups.get(g.id, [g.id])}))

    if not added:
        console.print("[yellow]No new gigs to push. Pipeline refreshed.[/yellow]")
        run_state.record_digest(
            ok=True,
            collected=collected,
            ranked_new=0,
            pushed=False,
            cross_source_deduped=deduped,
            stale_sources=stale_sources,
        )
        return

    result = dispatch(added, updated, source_warning=source_warning)
    pushed = result["pushed"]
    if result.get("followups"):
        console.print(f"  Follow-ups due: {result['followups']}")

    console.print(f"\n[green]✓[/green] Dispatched {result['gigs']} new gigs to pipeline")
    console.print(f"  Push: {'sent' if pushed else 'skipped (NTFY_TOPIC unset)'}")
    console.print(f"  Total unique seen: {seen_count()}")

    run_state.record_digest(
        ok=True,
        collected=collected,
        ranked_new=len(added),
        pushed=pushed,
        cross_source_deduped=deduped,
        stale_sources=stale_sources,
    )


@app.command()
def health(
    heartbeat: bool = typer.Option(
        False, "--heartbeat",
        help="Heartbeat mode: exit 1 + push a phone alert if no digest ran recently",
    ),
    max_age_hours: float = typer.Option(
        run_state.HEARTBEAT_MAX_AGE_HOURS, "--max-age-hours",
        help="Staleness threshold (hours) for --heartbeat",
    ),
):
    """Source scrape health and last digest heartbeat."""
    console.print(source_health.format_dashboard())
    console.print("")
    console.print(run_state.format_summary())

    if not heartbeat:
        return
    age = run_state.last_digest_age()
    if not run_state.heartbeat_is_stale(max_age_hours=max_age_hours):
        console.print(
            f"\n[green]Heartbeat OK[/green] — last digest "
            f"{age.total_seconds() / 3600:.1f}h ago",
        )
        return
    last = "never" if age is None else f"{age.total_seconds() / 3600:.0f}h ago"
    msg = (
        f"No digest run in {max_age_hours:.0f}h (last: {last}) — "
        "launchd job may be dead. Check `make status` and ~/Library/Logs/gigpilot."
    )
    console.print(f"\n[red]{msg}[/red]")
    push_failure(msg, title="GigPilot heartbeat STALE")
    raise typer.Exit(code=1)


@app.command(name="notify-failure")
def notify_failure(
    message: str = typer.Argument(..., help="Failure text to push to the phone"),
    title: str = typer.Option("GigPilot digest FAILED", "--title"),
):
    """Push a failure alert via ntfy (no-op when NTFY_TOPIC is unset).

    Used by scripts/daily_digest.sh as a fallback when the digest process
    dies before its own failure handler could push (import error, bad venv).
    """
    pushed = push_failure(message, title=title)
    console.print("pushed" if pushed else "skipped (NTFY_TOPIC unset or push failed)")


@app.command(name="feedback")
def show_feedback():
    """Show aggregated pass reasons from pipeline Notes."""
    added = feedback.sync_from_pipeline()
    if added:
        console.print(f"[dim]Synced {added} new pass entries[/dim]")
    console.print(feedback.format_summary())


@app.command(name="weekly-summary")
def weekly_summary():
    """Print a 1-screen week-in-review based on the pipeline."""
    rows = pipeline.parse()
    if not rows:
        console.print("[yellow]Pipeline is empty.[/yellow]")
        return

    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    console.print("[bold]GigPilot weekly summary[/bold]")
    console.print(f"Total in pipeline: {len(rows)}")
    for status in (
        "new", "saved", "drafted", "sent", "replied", "interview", "hired",
        "passed", "archived",
    ):
        if status in by_status:
            console.print(f"  {status:>10}: {by_status[status]}")

    console.print("")
    console.print(feedback.format_summary())

    from datetime import datetime, timedelta
    today = datetime.now()
    stale: list[pipeline.Row] = []
    for r in rows:
        if r.status != "sent" or not r.last_touched:
            continue
        lt = pipeline.parse_last_touched(r.last_touched, today)
        if lt and (today - lt) > timedelta(days=7):
            stale.append(r)
    if stale:
        console.print(f"\n[yellow]Stale (sent > 7 days, no follow-up):[/yellow]")
        for r in stale:
            console.print(f"  • {r.company} — {r.role} (sent {r.last_touched})")


@app.command()
def swipe(
    port: int = typer.Option(8799, "--port", help="Port to serve on"),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind address (0.0.0.0 = reachable from phone via Tailscale)"),
):
    """Phone-first job swiper. Run this, open the printed URL on your phone,
    tap Get jobs, then swipe: right to apply (opens a prepped email), left to
    pass. Decisions land in pipeline.md."""
    from jobpilot.gigs.server import run_server
    run_server(host=host, port=port)


@app.command()
def stats():
    """Show dedupe, pipeline, health, and last run."""
    console.print(f"Total unique gigs remembered (seen): {seen_count()}")
    rows = pipeline.parse()
    console.print(f"Pipeline rows: {len(rows)}")
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    for status, count in sorted(by_status.items(), key=lambda x: -x[1]):
        console.print(f"  {status:>12}: {count}")
    console.print("")
    console.print(run_state.format_summary())
    console.print("")
    console.print(source_health.format_dashboard())


# Back-compat for tests importing _scrapers from cli
def _scrapers():
    from jobpilot.gigs.core.collect import scraper_registry
    return scraper_registry()


def _include_upwork_exports():
    return include_upwork_exports()


if __name__ == "__main__":
    app()