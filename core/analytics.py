"""
Analytics — Export application data and generate daily digest reports.

Pulls data from ApplicationTracker (SQLite) and ActionRecorder (SQLite)
to produce CSV exports and Rich terminal reports.
"""

import csv
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from jobpilot.core.application_tracker import get_application_tracker
from jobpilot.core.config import DATA_DIR as JOBPILOT_DATA_DIR
from jobpilot.core.logger import get_logger
from jobpilot.core.opportunity_ledger import OpportunityLedger
from jobpilot.core.outcome_metrics import summarize_outcomes
from jobpilot.learning.action_recorder import get_action_recorder

log = get_logger(__name__)
console = Console()

DATA_DIR = JOBPILOT_DATA_DIR / "reports"


def _calibration_note(*, decided: int, review_eligible: bool) -> str:
    """Explain the outcome threshold without implying automatic retraining."""
    if review_eligible:
        return (
            f"Sample threshold reached ({decided} decided roles): calibration is "
            "eligible for explicit manual review only. JobPilot never changes "
            "weights automatically."
        )
    return (
        "Weights stay fixed: fewer than 20 explicit decided outcomes in this "
        "time window. JobPilot never changes weights automatically. Silence is "
        "censored, not counted as rejection."
    )


def export_csv(days: int = 30, output_path: Path | None = None) -> Path:
    """Export application history to CSV.

    Returns the path to the written CSV file.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = DATA_DIR / f"applications_{ts}.csv"

    tracker = get_application_tracker()
    apps = tracker.get_recent(limit=500)
    tracker.close()

    # Filter by date range
    cutoff = datetime.now() - timedelta(days=days)
    filtered = []
    for app in apps:
        try:
            applied = datetime.fromisoformat(app.applied_at)
            if applied >= cutoff:
                filtered.append(app)
        except (ValueError, AttributeError):
            filtered.append(app)  # include if can't parse date

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Job Title", "Company", "URL", "Status",
            "Applied At", "Step Reached",
        ])
        for app in filtered:
            writer.writerow([
                app.job_title,
                app.company,
                app.job_url,
                app.status,
                app.applied_at,
                getattr(app, "step_reached", ""),
            ])

    log.info(f"Exported {len(filtered)} applications to {output_path}")
    return output_path


def daily_digest(days: int = 7):
    """Generate a Rich terminal report summarising recent application activity."""
    tracker = get_application_tracker()
    stats = tracker.get_stats()
    recent = tracker.get_recent(limit=100)
    tracker.close()

    recorder = get_action_recorder()
    rec_stats = recorder.get_stats()

    # --- Header ---
    console.print(Panel.fit(
        f"[bold cyan]📊 JobPilot Report[/bold cyan]  ·  Last {days} days",
        border_style="cyan",
    ))

    # --- Summary Stats ---
    summary = Table(title="Summary", show_header=False, padding=(0, 2))
    summary.add_column("Metric", style="bold")
    summary.add_column("Value", justify="right")
    summary.add_row("Submitted", f"[green]{stats.get('submitted', 0)}[/green]")
    summary.add_row("Abandoned", f"[yellow]{stats.get('abandoned', 0)}[/yellow]")
    summary.add_row("In Progress", f"[cyan]{stats.get('in_progress', 0)}[/cyan]")
    summary.add_row("Total", str(stats.get("total", 0)))

    if stats.get("total", 0) > 0:
        success_rate = stats.get("submitted", 0) / stats["total"] * 100
        summary.add_row("Completion Rate", f"[bold]{success_rate:.0f}%[/bold]")

    if rec_stats:
        summary.add_row("Legacy Fields Approved", str(rec_stats.get("fields_approved", 0)))
        summary.add_row("Legacy Fields Edited", str(rec_stats.get("fields_edited", 0)))

    console.print(summary)

    ledger = OpportunityLedger()
    try:
        events = [
            {
                "opportunity_id": event.opportunity_id,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
                "source": event.source,
                "provenance": event.provenance,
            }
            for event in ledger.iter_events()
        ]
    finally:
        ledger.close()
    outcomes = summarize_outcomes(events, days=days)
    funnel = Table(title=f"Evidence funnel (last {days} days)")
    funnel.add_column("Stage")
    funnel.add_column("Events", justify="right")
    for stage in (
        "human_reply", "screen", "interview", "offer", "rejected",
        "withdrawn", "no_response",
    ):
        funnel.add_row(stage.replace("_", " ").title(), str(outcomes["stages"][stage]))
    funnel.add_row("Decided roles", str(outcomes["decided"]))
    console.print(funnel)
    cohort_bits = [
        *(f"channel {name}: {count}" for name, count in sorted(outcomes["channels"].items())),
        *(f"lane {name}: {count}" for name, count in sorted(outcomes["lanes"].items())),
    ]
    if cohort_bits:
        console.print("[dim]Cohort sample sizes: " + " · ".join(cohort_bits) + "[/dim]")
    console.print(
        "[dim]"
        + _calibration_note(
            decided=outcomes["decided"],
            review_eligible=outcomes["may_recalibrate"],
        )
        + "[/dim]"
    )

    # --- Recent Applications ---
    if recent:
        table = Table(title=f"\nRecent Applications (last {days} days)")
        table.add_column("#", style="dim", width=4)
        table.add_column("Job Title", max_width=40)
        table.add_column("Company", width=14)
        table.add_column("Status", width=12)
        table.add_column("Date", width=16)

        cutoff = datetime.now() - timedelta(days=days)
        count = 0
        for app in recent:
            try:
                applied = datetime.fromisoformat(app.applied_at)
                if applied < cutoff:
                    continue
            except (ValueError, AttributeError):
                pass

            count += 1
            status_style = {
                "submitted": "[green]✓ submitted[/green]",
                "abandoned": "[yellow]✗ abandoned[/yellow]",
                "started": "[cyan]… in progress[/cyan]",
            }.get(app.status, app.status)

            table.add_row(
                str(count),
                app.job_title[:40] if app.job_title else "—",
                app.company or "—",
                status_style,
                app.applied_at[:16] if app.applied_at else "—",
            )

        console.print(table)
    else:
        console.print("[dim]No applications recorded yet.[/dim]")

    console.print()
