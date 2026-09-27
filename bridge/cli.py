"""`jobpilot ledger`: bring legacy data into the one ledger, and look inside it."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from jobpilot.bridge import importers, sources
from jobpilot.core.config import DATA_DIR
from jobpilot.engine.adapters.sqlite_ledger import LEDGER_FILENAME, SQLiteLedger
from jobpilot.engine.adapters.toml_settings import SETTINGS_FILENAME, TomlSettings
from jobpilot.engine.domain import (
    SCREEN,
    BoardTarget,
    Lane,
    Screening,
    SettingsError,
    Status,
    utc_now,
)
from jobpilot.engine.ports import Boards
from jobpilot.engine.service.refresh import RefreshReport, refresh

app = typer.Typer(
    help="The one opportunity ledger: import legacy data and inspect it.",
    no_args_is_help=True,
)
console = Console()


def _modified_or_now(path: Path) -> str:
    """When a store was last written: the best guess for undated rows."""
    moment = (
        datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if path.exists()
        else datetime.now(UTC)
    )
    return moment.isoformat(timespec="seconds")


def _gigs_paths() -> tuple[Path, Path]:
    from jobpilot.gigs.core.paths import data_dir, pipeline_dir

    return pipeline_dir() / "pipeline.md", data_dir() / "first_seen.json"


def _status_counts(ledger: SQLiteLedger) -> Counter[tuple[Lane, Status]]:
    return Counter(
        (opportunity.lane, ledger.status(opportunity.id))
        for opportunity in ledger.opportunities()
    )


def _print_status(counts: Counter[tuple[Lane, Status]]) -> None:
    table = Table(title="Ledger by status")
    table.add_column("Status")
    for lane in Lane:
        table.add_column(lane.value.title() + "s", justify="right")
    table.add_column("Total", justify="right")
    for status in Status:
        row = [counts[(lane, status)] for lane in Lane]
        if any(row):
            table.add_row(status.value.replace("_", " "), *map(str, row), str(sum(row)))
    console.print(table)


@app.command("import")
def import_legacy(
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Show what would change without writing anything."
        ),
    ] = False,
    ledger: Annotated[
        Path | None, typer.Option(help="Ledger file (default: data/opportunities.db).")
    ] = None,
    queue: Annotated[Path | None, typer.Option(help="queue.json to import.")] = None,
    tracker: Annotated[
        Path | None, typer.Option(help="applications.db to import.")
    ] = None,
    pipeline: Annotated[
        Path | None, typer.Option(help="Gigs pipeline.md to import.")
    ] = None,
    first_seen: Annotated[
        Path | None, typer.Option(help="Gigs first_seen.json.")
    ] = None,
) -> None:
    """Import the tracker, queue, and gig pipeline into the ledger. Safe to re-run."""
    ledger_path = ledger or DATA_DIR / LEDGER_FILENAME
    queue = queue or DATA_DIR / "queue.json"
    tracker = tracker or DATA_DIR / "applications.db"
    if pipeline is None or first_seen is None:
        default_pipeline, default_first_seen = _gigs_paths()
        pipeline = pipeline or default_pipeline
        first_seen = first_seen or default_first_seen

    # The tracker goes first: it knows real application dates, so the queue's
    # undated "applied" marks are skipped when the tracker already has them.
    loaders: list[
        tuple[str, Callable[[], tuple[list[importers.ImportRecord], list[str]]]]
    ] = [
        (
            "tracker",
            lambda: importers.from_tracker(
                sources.read_tracker(tracker), default_time=_modified_or_now(tracker)
            ),
        ),
        (
            "queue",
            lambda: importers.from_queue(
                sources.read_queue(queue), default_time=_modified_or_now(queue)
            ),
        ),
        (
            "pipeline",
            lambda: importers.from_pipeline(
                sources.read_pipeline(pipeline),
                first_seen=sources.read_first_seen(first_seen),
                default_time=_modified_or_now(pipeline),
            ),
        ),
    ]
    batches: list[tuple[str, list[importers.ImportRecord], list[str]]] = []
    failures: list[str] = []
    for name, load in loaders:
        try:
            batches.append((name, *load()))
        except (OSError, ValueError, sqlite3.Error) as exc:
            failures.append(f"{name}: {exc}")

    with tempfile.TemporaryDirectory(prefix="jobpilot-ledger-dry-run-") as scratch:
        target = ledger_path
        if dry_run:
            # Rehearse on a copy so the real ledger is never touched.
            target = Path(scratch) / LEDGER_FILENAME
            if ledger_path.exists():
                with (
                    closing(sqlite3.connect(ledger_path)) as real,
                    closing(sqlite3.connect(target)) as copy,
                ):
                    real.backup(copy)
        with SQLiteLedger(target) as store:
            reports = [
                importers.write(store, records, source=name, skipped=skipped)
                for name, records, skipped in batches
            ]
            counts = _status_counts(store)

    _print_reports(reports, failures)
    _print_status(counts)
    if dry_run:
        console.print("[yellow]Dry run: nothing was written.[/yellow]")
    else:
        console.print(f"[green]Ledger updated:[/green] {ledger_path}")
    if failures:
        raise typer.Exit(1)


def _print_reports(reports: list[importers.ImportReport], failures: list[str]) -> None:
    table = Table(title="Import")
    for column in (
        "Source",
        "Rows",
        "New roles",
        "Events added",
        "Already recorded",
        "Skipped",
    ):
        table.add_column(column, justify="left" if column == "Source" else "right")
    for report in reports:
        cells: list[Any] = [
            report.records,
            report.new_opportunities,
            report.events_added,
            report.events_already_recorded,
            len(report.skipped),
        ]
        table.add_row(report.source, *map(str, cells))
    console.print(table)
    for report in reports:
        for reason in report.skipped:
            console.print(f"[yellow]skipped[/yellow] {reason}")
    for failure in failures:
        console.print(f"[red]could not read[/red] {failure}")


@app.command("status")
def ledger_status(
    ledger: Annotated[
        Path | None, typer.Option(help="Ledger file (default: data/opportunities.db).")
    ] = None,
) -> None:
    """Count opportunities by lane and status."""
    path = ledger or DATA_DIR / LEDGER_FILENAME
    if not path.exists():
        console.print("No ledger yet. Run `jobpilot ledger import` first.")
        raise typer.Exit(1)
    with SQLiteLedger(path) as store:
        _print_status(_status_counts(store))


# ----- boards -------------------------------------------------------------------

BOARD_PROVIDERS = frozenset({"greenhouse", "lever", "ashby"})
_ACTIONABLE = frozenset({Status.NEW, Status.SHORTLISTED, Status.READY})


def _make_boards() -> Boards:
    """The live board reader. Tests replace this."""
    from jobpilot.engine.adapters.ats_boards import HttpBoards

    return HttpBoards()


def board_targets(portals: Path, only: str | None = None) -> list[BoardTarget]:
    """Enabled Greenhouse, Lever, and Ashby boards from the legacy portals.json."""
    data = json.loads(portals.read_text()) if portals.exists() else []
    targets = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or item.get("enabled", True) is False:
            continue
        provider = str(item.get("portal", "")).strip().lower()
        token = str(item.get("value", "")).strip()
        if provider not in BOARD_PROVIDERS or not token:
            continue
        if only and only.lower() not in {token.lower(), f"{provider}:{token}".lower()}:
            continue
        targets.append(
            BoardTarget(provider, token, str(item.get("label") or token).strip())
        )
    return targets


@contextmanager
def _ledger_for(path: Path, dry_run: bool) -> Iterator[SQLiteLedger]:
    """The real ledger, or a throwaway copy of it for a dry run."""
    with tempfile.TemporaryDirectory(prefix="jobpilot-ledger-dry-run-") as scratch:
        target = path
        if dry_run:
            target = Path(scratch) / LEDGER_FILENAME
            if path.exists():
                with (
                    closing(sqlite3.connect(path)) as real,
                    closing(sqlite3.connect(target)) as copy,
                ):
                    real.backup(copy)
        with SQLiteLedger(target) as store:
            yield store


def _print_refresh(report: RefreshReport) -> None:
    table = Table(title="Refresh")
    for column in (
        "Boards",
        "Failed",
        "Postings",
        "New",
        "Reopened",
        "Closed",
        "Passed rules",
        "Failed rules",
    ):
        table.add_column(column, justify="right")
    table.add_row(
        *map(
            str,
            (
                report.boards,
                len(report.failed_boards),
                report.postings,
                report.new_roles,
                report.reopened,
                report.closed,
                report.passed,
                report.failed,
            ),
        )
    )
    console.print(table)
    if report.failures_by_rule:
        reasons = ", ".join(
            f"{rule} {count}" for rule, count in report.failures_by_rule.most_common()
        )
        console.print(f"Failed by rule: {reasons}", markup=False)
    if report.unknowns_by_rule:
        unsure = ", ".join(
            f"{rule} {count}" for rule, count in report.unknowns_by_rule.most_common()
        )
        console.print(f"Unsure (kept for review): {unsure}", markup=False)
    for failure in report.failed_boards:
        console.print(f"[yellow]board skipped[/yellow] {failure}")


@app.command("refresh")
def refresh_boards(
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Show what would change without writing anything."
        ),
    ] = False,
    ledger: Annotated[
        Path | None, typer.Option(help="Ledger file (default: data/opportunities.db).")
    ] = None,
    portals: Annotated[
        Path | None, typer.Option(help="Boards to read (default: data/portals.json).")
    ] = None,
    settings: Annotated[
        Path | None, typer.Option(help="Settings file (default: data/jobpilot.toml).")
    ] = None,
    only: Annotated[
        str | None, typer.Option(help="Read one board, by token or provider:token.")
    ] = None,
) -> None:
    """Read every job board, store full postings, and screen them against your rules."""
    try:
        loaded, warnings = TomlSettings(settings or DATA_DIR / SETTINGS_FILENAME).load()
    except SettingsError as exc:
        for problem in exc.problems:
            console.print(f"[red]✗[/red] {problem}")
        raise typer.Exit(1) from None
    for warning in warnings:
        console.print(f"[yellow]![/yellow] {warning}")
    targets = board_targets(portals or DATA_DIR / "portals.json", only)
    if not targets:
        console.print("No Greenhouse, Lever, or Ashby boards to read.")
        raise typer.Exit(1)
    with _ledger_for(ledger or DATA_DIR / LEDGER_FILENAME, dry_run) as store:
        report = refresh(store, _make_boards(), targets, loaded, now=utc_now())
    _print_refresh(report)
    console.print(
        "[yellow]Dry run: nothing was written.[/yellow]"
        if dry_run
        else "[green]Ledger updated.[/green]"
    )


@app.command("screened")
def screened(
    ledger: Annotated[
        Path | None, typer.Option(help="Ledger file (default: data/opportunities.db).")
    ] = None,
    failed: Annotated[
        bool, typer.Option("--failed", help="Show roles that broke a rule, and why.")
    ] = False,
    limit: Annotated[int, typer.Option(help="Most rows to show.")] = 50,
) -> None:
    """Open roles that pass your rules, or with --failed, the ones that don't and why."""
    path = ledger or DATA_DIR / LEDGER_FILENAME
    if not path.exists():
        console.print("No ledger yet. Run `jobpilot ledger refresh` first.")
        raise typer.Exit(1)
    rows: list[tuple[str, str, str, str]] = []
    with SQLiteLedger(path) as store:
        for opportunity in store.opportunities(Lane.JOB):
            assessment = store.latest_assessment(opportunity.id, SCREEN)
            if assessment is None or store.status(opportunity.id) not in _ACTIONABLE:
                continue
            screening = Screening.from_dict(assessment.result)
            if screening.passed == failed:
                continue
            posting = store.posting(opportunity.id)
            where = (
                " / ".join(posting.all_locations[:2])
                if posting
                else opportunity.location
            )
            if posting and posting.workplace.value != "unknown":
                where = f"{posting.workplace.value}: {where}"
            notes = screening.failures if failed else screening.unknowns
            rows.append(
                (
                    opportunity.company,
                    opportunity.title,
                    where,
                    "; ".join(check.detail for check in notes),
                )
            )
    rows.sort(key=lambda row: (row[0].lower(), row[1].lower()))
    table = Table(title=f"{'Failed' if failed else 'Passed'} your rules ({len(rows)})")
    for column in ("Company", "Role", "Where", "Why" if failed else "Check"):
        table.add_column(column, overflow="fold")
    for row in rows[:limit]:
        table.add_row(*row)
    console.print(table)
