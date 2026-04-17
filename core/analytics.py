"""
Analytics — Export application data and generate daily digest reports.

Pulls data from ApplicationTracker (SQLite) and ActionRecorder (SQLite)
to produce CSV exports and Rich terminal reports.
"""

import csv
import io
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from jobpilot.core.application_tracker import get_application_tracker
from jobpilot.learning.action_recorder import get_action_recorder
from jobpilot.core.logger import get_logger

log = get_logger(__name__)
console = Console()

DATA_DIR = Path(__file__).parent.parent / "data" / "reports"


def export_csv(days: int = 30, output_path: Optional[Path] = None) -> Path:
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
            started = datetime.fromisoformat(app.started_at)
            if started >= cutoff:
                filtered.append(app)
        except (ValueError, AttributeError):
            filtered.append(app)  # include if can't parse date

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Job Title", "Company/Platform", "URL", "Status",
            "Started At", "Step Reached",
        ])
        for app in filtered:
            writer.writerow([
                app.job_title,
                app.platform,
                app.url,
                app.status,
                app.started_at,
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
        summary.add_row("Fields Auto-Filled", str(rec_stats.get("fields_approved", 0)))
        summary.add_row("Fields Edited", str(rec_stats.get("fields_edited", 0)))

    console.print(summary)

    # --- Recent Applications ---
    if recent:
        table = Table(title=f"\nRecent Applications (last {days} days)")
        table.add_column("#", style="dim", width=4)
        table.add_column("Job Title", max_width=40)
        table.add_column("Platform", width=10)
        table.add_column("Status", width=12)
        table.add_column("Date", width=16)

        cutoff = datetime.now() - timedelta(days=days)
        count = 0
        for app in recent:
            try:
                started = datetime.fromisoformat(app.started_at)
                if started < cutoff:
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
                app.platform or "—",
                status_style,
                app.started_at[:16] if app.started_at else "—",
            )

        console.print(table)
    else:
        console.print("[dim]No applications recorded yet.[/dim]")

    console.print()
