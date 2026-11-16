"""`jobpilot employers`: index companies, HR teams, gates, and form requirements."""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from jobpilot.bridge.cli import board_targets
from jobpilot.bridge.employer_importers import (
    EmployerImportReport,
    import_all_sources,
    read_opportunities,
)
from jobpilot.core.atomic_io import atomic_write_text
from jobpilot.core.config import DATA_DIR
from jobpilot.engine.adapters.ats_forms import HttpFormSchemas
from jobpilot.engine.adapters.sqlite_employers import (
    EMPLOYERS_FILENAME,
    SQLiteEmployerStore,
)
from jobpilot.engine.domain import (
    EmployerSummary,
    GateKind,
    normalize_employer_key,
    summarize_employer,
    utc_now,
)
from jobpilot.engine.ports import Boards, FormSchemas
from jobpilot.engine.service.employers import (
    CollectReport,
    collect_employer_schemas,
    sample_board_opportunities,
)

app = typer.Typer(
    help="Employer, HR team, and pre-submit application requirement intelligence.",
    no_args_is_help=True,
)
console = Console()


def _make_boards() -> Boards:
    from jobpilot.engine.adapters.ats_boards import HttpBoards

    return HttpBoards()


def _make_fetcher(delay_ms: int = 0) -> FormSchemas:
    return HttpFormSchemas(delay_seconds=max(0.0, delay_ms / 1000.0))


@contextmanager
def _store_for(path: Path, dry_run: bool) -> Iterator[SQLiteEmployerStore]:
    """Open the employer store, or a temporary backup copy during --dry-run."""
    with tempfile.TemporaryDirectory(prefix="jobpilot-employers-dry-run-") as scratch:
        target = path
        if dry_run:
            target = Path(scratch) / EMPLOYERS_FILENAME
            if path.exists():
                with (
                    closing(sqlite3.connect(path)) as real,
                    closing(sqlite3.connect(target)) as copy,
                ):
                    real.backup(copy)
        with SQLiteEmployerStore(target) as store:
            yield store


def _summaries(store: SQLiteEmployerStore) -> list[EmployerSummary]:
    contacts_by: dict[str, list] = {}
    for c in store.contacts():
        contacts_by.setdefault(c.employer_key, []).append(c)
    obs_by: dict[str, list] = {}
    for o in store.observations():
        obs_by.setdefault(o.employer_key, []).append(o)
    snaps_by: dict[str, list] = {}
    for s in store.snapshots():
        snaps_by.setdefault(s.employer_key, []).append(s)

    return [
        summarize_employer(
            emp,
            contacts_by.get(emp.employer_key, ()),
            obs_by.get(emp.employer_key, ()),
            snaps_by.get(emp.employer_key, ()),
        )
        for emp in store.employers()
    ]


def _safe_csv(value: object) -> str:
    text = str(value if value is not None else "")
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{text}"
    return text


def _write_private_export(path: Path, content: str) -> None:
    atomic_write_text(path, content)
    if os.name == "posix":
        os.chmod(path, 0o600)


def _print_import_report(report: EmployerImportReport, dry_run: bool) -> None:
    table = Table(title="Employer import")
    for col in (
        "Sources",
        "Employers touched",
        "HR msgs counted",
        "Observations added",
        "Snapshots",
    ):
        table.add_column(col, justify="right")
    table.add_row(
        str(len(report.sources_read)),
        str(report.employers_upserted),
        str(report.contacts_counted),
        str(report.observations_added),
        str(report.snapshots_recorded),
    )
    console.print(table)
    for w in report.warnings:
        console.print(f"[yellow]warning[/yellow] {w}", markup=False)
    console.print(
        "[yellow]Dry run: nothing was written.[/yellow]"
        if dry_run
        else "[green]Employer store updated.[/green]"
    )


@app.command("import")
def import_employers(
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Preview import counts without writing employers.db."
        ),
    ] = False,
    store: Annotated[
        Path | None,
        typer.Option(help="Employer store file (default: data/employers.db)."),
    ] = None,
    data_dir: Annotated[
        Path | None,
        typer.Option(
            help="JobPilot data folder containing portals.json, applications.db, opportunities.db."
        ),
    ] = None,
    job_search_dir: Annotated[
        Path | None,
        typer.Option(
            help="Optional campaign folder (e.g. ~/job-search) with preflight/status/mail files."
        ),
    ] = None,
    gmail_jsonl: Annotated[
        Path | None,
        typer.Option(
            help="Optional JSONL of Gmail metadata objects (From/Reply-To/Subject/Date)."
        ),
    ] = None,
    candidate_email: Annotated[
        list[str] | None,
        typer.Option(
            help="Applicant email address to exclude from HR contacts (repeatable)."
        ),
    ] = None,
) -> None:
    """Import portals, tracker, campaign preflights/holds, and Gmail HR metadata."""
    store_path = store or DATA_DIR / EMPLOYERS_FILENAME
    source_data_dir = data_dir or DATA_DIR
    try:
        with _store_for(store_path, dry_run) as emp_store:
            report = import_all_sources(
                emp_store,
                data_dir=source_data_dir,
                job_search_dir=job_search_dir,
                gmail_jsonl=gmail_jsonl,
                candidate_emails=candidate_email or (),
            )
    except (OSError, ValueError) as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from None
    _print_import_report(report, dry_run)


@app.command("collect")
def collect_forms(
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Preview live form collection without modifying employers.db.",
        ),
    ] = False,
    store: Annotated[
        Path | None,
        typer.Option(help="Employer store file (default: data/employers.db)."),
    ] = None,
    ledger: Annotated[
        Path | None,
        typer.Option(
            help="Opportunities DB to read read-only (default: data/opportunities.db)."
        ),
    ] = None,
    portals: Annotated[
        Path | None,
        typer.Option(
            help="Boards JSON to sample unindexed boards from (default: data/portals.json)."
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option(help="Maximum live ATS HTTP form lookups in this run."),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option(
            help="Filter by ATS provider (e.g. greenhouse, ashby, lever, workday)."
        ),
    ] = None,
    company: Annotated[
        str | None,
        typer.Option(help="Filter to a single company name."),
    ] = None,
    delay_ms: Annotated[
        int,
        typer.Option(help="Polite delay in milliseconds between live HTTP requests."),
    ] = 100,
    refresh_existing: Annotated[
        bool,
        typer.Option(
            "--refresh-existing", help="Re-fetch roles that already have a snapshot."
        ),
    ] = False,
    sample_boards: Annotated[
        bool,
        typer.Option(
            "--sample-boards/--no-sample-boards",
            help="Sample 1 posting per enabled portals.json board missing from opportunities.",
        ),
    ] = True,
) -> None:
    """Fetch public ATS application schemas (Greenhouse, Ashby, Lever) and record portal gates."""
    store_path = store or DATA_DIR / EMPLOYERS_FILENAME
    ledger_path = ledger or DATA_DIR / "opportunities.db"
    portals_path = portals or DATA_DIR / "portals.json"

    opps = read_opportunities(ledger_path)
    board_warnings: list[str] = []

    if sample_boards and portals_path.exists():
        known_boards = {
            f"{o.provider}:{o.tenant}".lower() for o in opps if o.provider and o.tenant
        }
        prov_filter = (provider or "").strip().lower()
        comp_filter = normalize_employer_key(company or "")
        targets = [
            t
            for t in board_targets(portals_path)
            if f"{t.provider}:{t.token}".lower() not in known_boards
            and (not prov_filter or t.provider == prov_filter)
            and (not comp_filter or normalize_employer_key(t.name) == comp_filter)
        ]
        if targets:
            sampled, board_warnings = sample_board_opportunities(
                _make_boards(), targets, per_board=1
            )
            opps.extend(sampled)

    with _store_for(store_path, dry_run) as emp_store:
        report: CollectReport = collect_employer_schemas(
            None,
            emp_store,
            _make_fetcher(delay_ms),
            now=utc_now(),
            limit=limit,
            provider=provider,
            company=company,
            refresh_existing=refresh_existing,
            extra_targets=opps,
        )

    table = Table(title="Employer form collection")
    for col in (
        "Inspected",
        "HTTP fetched",
        "Skipped",
        "Inferred gates",
        "Unsupported",
        "Errors",
        "Obs added",
    ):
        table.add_column(col, justify="right")
    table.add_row(
        str(report.inspected),
        str(report.fetched),
        str(report.skipped_existing),
        str(report.inferred_gates),
        str(report.unsupported),
        str(report.errors),
        str(report.observations_added),
    )
    console.print(table)
    for w in [*board_warnings, *report.failures[:10]]:
        console.print(f"[yellow]note[/yellow] {w}", markup=False)
    console.print(
        "[yellow]Dry run: nothing was written.[/yellow]"
        if dry_run
        else "[green]Collection saved.[/green]"
    )


@app.command("status")
def status_cmd(
    store: Annotated[
        Path | None,
        typer.Option(help="Employer store file (default: data/employers.db)."),
    ] = None,
) -> None:
    """Summarize tracked companies, HR contact channels, gates, and top custom asks."""
    store_path = store or DATA_DIR / EMPLOYERS_FILENAME
    if not store_path.exists():
        console.print("No employer store yet. Run `jobpilot employers import` first.")
        raise typer.Exit(1)

    with SQLiteEmployerStore(store_path) as emp_store:
        summaries = _summaries(emp_store)
        contacts = emp_store.contacts()
        obs = emp_store.observations()
        snaps = emp_store.snapshots()

    tier_counts = Counter(s.friction_tier.value for s in summaries)
    gate_counts = Counter(
        g.value for s in summaries for g in s.gates if g is not GateKind.NONE
    )
    channel_counts = Counter(c.channel_kind.value for c in contacts)
    beyond_counts = Counter(
        o.category.value
        for o in obs
        if o.is_beyond_standard and o.requiredness != "optional"
    )

    overview = Table(title="Employer Intelligence Overview")
    for col in (
        "Employers",
        "HR contacts",
        "Direct HR",
        "Team HR",
        "Observations",
        "Live snapshots",
    ):
        overview.add_column(col, justify="right")
    overview.add_row(
        str(len(summaries)),
        str(len(contacts)),
        str(channel_counts.get("direct_person", 0)),
        str(channel_counts.get("company_recruiting", 0)),
        str(len(obs)),
        str(sum(1 for s in snaps if s.status == "ok")),
    )
    console.print(overview)
    console.print(
        "Friction tiers: "
        + ", ".join(
            f"{k}={tier_counts.get(k, 0)}"
            for k in ("low_friction", "custom_questions", "gated")
        )
    )
    if gate_counts:
        console.print(
            "Gates observed (companies): "
            + ", ".join(f"{k}={v}" for k, v in gate_counts.most_common())
        )
    if beyond_counts:
        top = Table(title="Top Beyond-Standard Pre-Submit Categories")
        top.add_column("Category")
        top.add_column("Observations", justify="right")
        for cat, cnt in beyond_counts.most_common(12):
            top.add_row(cat, str(cnt))
        console.print(top)


@app.command("list")
def list_employers(
    store: Annotated[
        Path | None,
        typer.Option(help="Employer store file (default: data/employers.db)."),
    ] = None,
    gate: Annotated[str | None, typer.Option(help="Filter by GateKind value.")] = None,
    provider: Annotated[
        str | None, typer.Option(help="Filter by ATS provider.")
    ] = None,
    tier: Annotated[str | None, typer.Option(help="Filter by FrictionTier.")] = None,
    has_direct_hr: Annotated[
        bool,
        typer.Option(
            "--has-direct-hr", help="Only employers with direct or company HR emails."
        ),
    ] = False,
    limit: Annotated[int, typer.Option(help="Maximum rows to display.")] = 40,
) -> None:
    """List tracked companies filtered by gate, provider, friction tier, or HR contacts."""
    store_path = store or DATA_DIR / EMPLOYERS_FILENAME
    if not store_path.exists():
        console.print("No employer store yet. Run `jobpilot employers import` first.")
        raise typer.Exit(1)

    with SQLiteEmployerStore(store_path) as emp_store:
        summaries = _summaries(emp_store)

    filtered: list[EmployerSummary] = []
    for s in summaries:
        if gate and gate.lower() not in {g.value for g in s.gates}:
            continue
        if provider and provider.lower() not in s.employer.providers:
            continue
        if tier and s.friction_tier.value != tier.lower():
            continue
        if has_direct_hr and not (s.direct_contacts or s.team_contacts):
            continue
        filtered.append(s)

    filtered.sort(
        key=lambda s: (
            -s.observation_count,
            -len(s.direct_contacts),
            s.employer.display_name.lower(),
        )
    )
    table = Table(title=f"Employers ({len(filtered)} matched)")
    for col in (
        "Company",
        "Providers",
        "Tier",
        "Gates",
        "Direct/Team HR",
        "Beyond-Std Cats",
        "Obs",
    ):
        table.add_column(col, overflow="fold")
    for s in filtered[:limit]:
        hr_cnt = len(s.direct_contacts) + len(s.team_contacts)
        table.add_row(
            s.employer.display_name,
            ", ".join(s.employer.providers) or "—",
            s.friction_tier.value,
            ", ".join(g.value for g in s.gates) or "—",
            str(hr_cnt) if hr_cnt else "0",
            ", ".join(s.beyond_standard_categories[:4]) or "—",
            str(s.observation_count),
        )
    console.print(table)


@app.command("show")
def show_employer(
    company: Annotated[
        str, typer.Argument(help="Company name or normalized employer key.")
    ],
    store: Annotated[
        Path | None,
        typer.Option(help="Employer store file (default: data/employers.db)."),
    ] = None,
) -> None:
    """Show full gates, HR team contacts, and required fields for one company."""
    store_path = store or DATA_DIR / EMPLOYERS_FILENAME
    if not store_path.exists():
        console.print("No employer store yet. Run `jobpilot employers import` first.")
        raise typer.Exit(1)

    key = normalize_employer_key(company)
    with SQLiteEmployerStore(store_path) as emp_store:
        emp = emp_store.get_employer(key)
        if emp is None:
            console.print(f"No employer found for {company!r} (key={key!r}).")
            raise typer.Exit(1)
        contacts = emp_store.contacts(key)
        obs = emp_store.observations(key)
        snaps = emp_store.snapshots(key)

    summary = summarize_employer(emp, contacts, obs, snaps)
    console.print(f"{emp.display_name} ({emp.employer_key})", markup=False)
    console.print(
        f"Tier: {summary.friction_tier.value} | Providers: {', '.join(emp.providers) or '—'} | "
        f"Tokens: {', '.join(emp.board_tokens) or '—'} | Gates: {', '.join(g.value for g in summary.gates) or '—'}"
    )
    if contacts:
        ct = Table(title="HR / Recruiting Contacts")
        for col in ("Channel", "Name", "Email", "Msgs", "Sample Subject"):
            ct.add_column(col, overflow="fold", no_wrap=(col == "Email"))
        for c in contacts:
            ct.add_row(
                c.channel_kind.value,
                c.display_name or "—",
                c.email,
                str(c.message_count),
                c.sample_subject,
            )
        console.print(ct)

    beyond = [o for o in obs if o.is_beyond_standard]
    if beyond:
        ot = Table(title=f"Beyond-Standard Requirements & Gates ({len(beyond)})")
        for col in ("Role", "Category", "Req", "Gate", "Label / Ask"):
            ot.add_column(col, overflow="fold")
        for o in beyond[:40]:
            ot.add_row(
                o.role_title or "—",
                o.category.value,
                o.requiredness,
                o.gate_kind.value if o.gate_kind else "—",
                o.label,
            )
        console.print(ot)


@app.command("export")
def export_intel(
    store: Annotated[
        Path | None,
        typer.Option(help="Employer store file (default: data/employers.db)."),
    ] = None,
    out_dir: Annotated[
        Path | None,
        typer.Option(help="Output directory (default: data/employer-intel)."),
    ] = None,
) -> None:
    """Export EMPLOYERS.md, employers.json, requirements.csv, and hr-contacts.csv."""
    store_path = store or DATA_DIR / EMPLOYERS_FILENAME
    if not store_path.exists():
        console.print("No employer store yet. Run `jobpilot employers import` first.")
        raise typer.Exit(1)

    target_dir = out_dir or DATA_DIR / "employer-intel"
    target_dir.mkdir(parents=True, exist_ok=True)

    with SQLiteEmployerStore(store_path) as emp_store:
        summaries = _summaries(emp_store)
        contacts = emp_store.contacts()
        obs = emp_store.observations()

    summaries.sort(
        key=lambda s: (-s.observation_count, s.employer.display_name.lower())
    )

    # 1. JSON export
    payload = {
        "generated_at": utc_now(),
        "employer_count": len(summaries),
        "contact_count": len(contacts),
        "observation_count": len(obs),
        "employers": [
            {
                "employer_key": s.employer.employer_key,
                "display_name": s.employer.display_name,
                "providers": list(s.employer.providers),
                "board_tokens": list(s.employer.board_tokens),
                "domains": list(s.employer.domains),
                "friction_tier": s.friction_tier.value,
                "gates": [g.value for g in s.gates],
                "role_count": s.role_count,
                "observation_count": s.observation_count,
                "beyond_standard_categories": list(s.beyond_standard_categories),
                "hr_contacts": [
                    {
                        "channel_kind": c.channel_kind.value,
                        "email": c.email,
                        "display_name": c.display_name,
                        "domain": c.domain,
                        "message_count": c.message_count,
                        "last_seen": c.last_seen,
                    }
                    for c in (*s.direct_contacts, *s.team_contacts, *s.ats_contacts)
                ],
            }
            for s in summaries
        ],
    }
    _write_private_export(
        target_dir / "employers.json",
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )

    # 2. requirements.csv (neutralizing CSV formula prefixes)
    req_buf = io.StringIO()
    req_writer = csv.writer(req_buf)
    req_writer.writerow(
        [
            "employer_key",
            "employer_name",
            "provider",
            "role_title",
            "role_url",
            "category",
            "requiredness",
            "gate_kind",
            "is_beyond_standard",
            "label",
            "field_type",
            "source_kind",
            "observed_at",
        ]
    )
    for o in obs:
        req_writer.writerow(
            [
                _safe_csv(o.employer_key),
                _safe_csv(o.employer_name),
                _safe_csv(o.provider),
                _safe_csv(o.role_title),
                _safe_csv(o.role_url),
                _safe_csv(o.category.value),
                _safe_csv(o.requiredness),
                _safe_csv(o.gate_kind.value if o.gate_kind else ""),
                "true" if o.is_beyond_standard else "false",
                _safe_csv(o.label),
                _safe_csv(o.field_type),
                _safe_csv(o.source_kind),
                _safe_csv(o.observed_at),
            ]
        )
    _write_private_export(target_dir / "requirements.csv", req_buf.getvalue())

    # 3. hr-contacts.csv
    hr_buf = io.StringIO()
    hr_writer = csv.writer(hr_buf)
    hr_writer.writerow(
        [
            "employer_key",
            "channel_kind",
            "display_name",
            "email",
            "domain",
            "message_count",
            "first_seen",
            "last_seen",
            "sample_subject",
        ]
    )
    for c in contacts:
        hr_writer.writerow(
            [
                _safe_csv(c.employer_key),
                _safe_csv(c.channel_kind.value),
                _safe_csv(c.display_name),
                _safe_csv(c.email),
                _safe_csv(c.domain),
                str(c.message_count),
                _safe_csv(c.first_seen),
                _safe_csv(c.last_seen),
                _safe_csv(c.sample_subject),
            ]
        )
    _write_private_export(target_dir / "hr-contacts.csv", hr_buf.getvalue())

    # 4. EMPLOYERS.md summary
    tier_counts = Counter(s.friction_tier.value for s in summaries)
    gate_counts = Counter(
        g.value for s in summaries for g in s.gates if g is not GateKind.NONE
    )
    lines = [
        "# Employer, HR Team, and Pre-Submit Requirements Index",
        "",
        f"Generated: {utc_now()} | Employers: **{len(summaries)}** | HR contacts: **{len(contacts)}** | Requirement observations: **{len(obs)}**",
        "",
        "## Friction & Gate Breakdown",
        "",
        f"- **Low friction:** {tier_counts.get('low_friction', 0)}",
        f"- **Custom questions:** {tier_counts.get('custom_questions', 0)}",
        f"- **Gated:** {tier_counts.get('gated', 0)}",
        "",
        "| Gate | Companies |",
        "| --- | ---: |",
    ]
    for g_name, g_cnt in gate_counts.most_common():
        lines.append(f"| `{g_name}` | {g_cnt} |")
    lines.extend(
        [
            "",
            "## Top Tracked Employers",
            "",
            "| Company | Providers | Tier | Gates | Direct/Team HR | Beyond-Standard Categories | Obs |",
            "| --- | --- | --- | --- | ---: | --- | ---: |",
        ]
    )
    for s in summaries[:150]:
        hr_cnt = len(s.direct_contacts) + len(s.team_contacts)
        provs = ", ".join(s.employer.providers) or "—"
        gates = ", ".join(g.value for g in s.gates) or "—"
        cats = ", ".join(s.beyond_standard_categories[:5]) or "—"
        lines.append(
            f"| {s.employer.display_name} | {provs} | {s.friction_tier.value} | {gates} | {hr_cnt} | {cats} | {s.observation_count} |"
        )
    _write_private_export(target_dir / "EMPLOYERS.md", "\n".join(lines) + "\n")

    console.print(f"[green]Exported employer intelligence to {target_dir}[/green]")
