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

from jobpilot.bridge import sources
from jobpilot.bridge.cli import board_targets
from jobpilot.bridge.employer_importers import (
    EmployerImportReport,
    import_all_sources,
    read_opportunities,
    read_status_opportunities,
)
from jobpilot.core.atomic_io import atomic_write_text
from jobpilot.core.config import DATA_DIR
from jobpilot.engine.adapters.ats_forms import HttpFormSchemas
from jobpilot.engine.adapters.sqlite_employers import (
    EMPLOYERS_FILENAME,
    SQLiteEmployerStore,
)
from jobpilot.engine.adapters.toml_settings import SETTINGS_FILENAME, TomlSettings
from jobpilot.engine.domain import (
    PUBLIC_API_PROVIDERS,
    BoardTarget,
    EmployerSummary,
    GateKind,
    OutreachSender,
    QueueRoute,
    SettingsError,
    build_action_queue,
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
    one_per_employer: Annotated[
        bool,
        typer.Option(
            "--one-per-employer/--all-roles",
            help="Collect at most one live/inferred role per company across this pass.",
        ),
    ] = False,
    from_store_boards: Annotated[
        bool,
        typer.Option(
            "--from-store-boards/--no-from-store-boards",
            help="Also sample 1 posting per unsnapshotted board token already in employers.db.",
        ),
    ] = False,
) -> None:
    """Fetch public ATS application schemas (Greenhouse, Ashby, Lever) and record portal gates."""
    store_path = store or DATA_DIR / EMPLOYERS_FILENAME
    ledger_path = ledger or DATA_DIR / "opportunities.db"
    portals_path = portals or DATA_DIR / "portals.json"

    opps = read_opportunities(ledger_path)
    board_warnings: list[str] = []
    prov_filter = (provider or "").strip().lower()
    comp_filter = normalize_employer_key(company or "")
    extra_targets: list[BoardTarget] = []
    seen_board_keys: set[str] = set()

    if from_store_boards and store_path.exists():
        with SQLiteEmployerStore(store_path) as ro_store:
            ok_employers = {
                s.employer_key for s in ro_store.snapshots() if s.status == "ok"
            }
            for emp in ro_store.employers():
                if emp.employer_key in ok_employers:
                    continue
                if comp_filter and emp.employer_key != comp_filter:
                    continue
                for bt in emp.board_tokens:
                    if ":" not in bt:
                        continue
                    b_prov, b_tok = bt.split(":", 1)
                    if b_prov not in PUBLIC_API_PROVIDERS or not b_tok:
                        continue
                    if prov_filter and b_prov != prov_filter:
                        continue
                    bk = f"{b_prov}:{b_tok}".lower()
                    if bk not in seen_board_keys:
                        seen_board_keys.add(bk)
                        extra_targets.append(
                            BoardTarget(b_prov, b_tok, emp.display_name)
                        )

    if sample_boards and portals_path.exists():
        known_boards = {
            f"{o.provider}:{o.tenant}".lower() for o in opps if o.provider and o.tenant
        }
        for t in board_targets(portals_path):
            bk = f"{t.provider}:{t.token}".lower()
            if bk in known_boards or bk in seen_board_keys:
                continue
            if prov_filter and t.provider != prov_filter:
                continue
            if comp_filter and normalize_employer_key(t.name) != comp_filter:
                continue
            seen_board_keys.add(bk)
            extra_targets.append(t)

    if extra_targets:
        sampled, board_warnings = sample_board_opportunities(
            _make_boards(), extra_targets, per_board=1
        )
        opps = [*sampled, *opps]

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
            one_per_employer=one_per_employer,
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


def _read_private_profile(path: Path) -> dict:
    """Read the gitignored applicant profile (data/profile.json); {} when absent or unreadable."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _text(profile: dict, key: str) -> str:
    return str(profile.get(key) or "").strip()


def _outreach_sender(profile: dict) -> OutreachSender:
    """Outreach signature and location note, read at runtime from the private profile.

    ``outreach_availability`` (optional) is used verbatim. Otherwise the note
    is built from ``city``/``state`` and ``open_to_relocation``.
    """
    name = " ".join(
        p for p in (_text(profile, "first_name"), _text(profile, "last_name")) if p
    )
    contact_line = " | ".join(
        p for p in (_text(profile, "phone"), _text(profile, "email")) if p
    )
    availability = _text(profile, "outreach_availability")
    if not availability:
        place = ", ".join(
            p for p in (_text(profile, "city"), _text(profile, "state")) if p
        )
        parts = [f"based in {place}"] if place else []
        if profile.get("open_to_relocation") is True:
            parts.append("open to relocation")
        availability = "; ".join(parts)
    return OutreachSender(
        name=name, contact_line=contact_line, availability=availability
    )


@app.command("plan-queue")
def plan_queue(
    store: Annotated[
        Path | None,
        typer.Option(help="Employer store file (default: data/employers.db)."),
    ] = None,
    ledger: Annotated[
        list[Path] | None,
        typer.Option(help="Opportunities DB(s) to read read-only (repeatable)."),
    ] = None,
    status_md: Annotated[
        list[Path] | None,
        typer.Option(
            help="Campaign STATUS.md tables with blocked/held roles (repeatable)."
        ),
    ] = None,
    tracker: Annotated[
        Path | None,
        typer.Option(
            help="Applications DB to exclude already-applied companies (default: data/applications.db)."
        ),
    ] = None,
    profile_json: Annotated[
        Path | None,
        typer.Option(
            help="Optional canonical-profile.json path for standing policy metadata."
        ),
    ] = None,
    settings: Annotated[
        Path | None,
        typer.Option(help="Optional jobpilot.toml path for company exclusions."),
    ] = None,
    applicant_profile: Annotated[
        Path | None,
        typer.Option(
            help="Private, gitignored profile.json for outreach signature and location (default: data/profile.json)."
        ),
    ] = None,
    out_dir: Annotated[
        Path | None,
        typer.Option(help="Output directory (default: data/employer-intel)."),
    ] = None,
) -> None:
    """Build standing-policy.json, 1-per-company ACTION-QUEUE.md/json, and LINKEDIN-PART2-BRIEF.md."""
    store_path = store or DATA_DIR / EMPLOYERS_FILENAME
    if not store_path.exists():
        console.print("No employer store yet. Run `jobpilot employers import` first.")
        raise typer.Exit(1)

    target_dir = out_dir or DATA_DIR / "employer-intel"
    target_dir.mkdir(parents=True, exist_ok=True)
    tracker_path = tracker or DATA_DIR / "applications.db"
    settings_path = settings or DATA_DIR / SETTINGS_FILENAME
    ledger_paths = ledger or [DATA_DIR / "opportunities.db"]

    # 1. Load summaries from employers.db
    with SQLiteEmployerStore(store_path) as emp_store:
        summaries_list = _summaries(emp_store)
        contacts = emp_store.contacts()
        obs = emp_store.observations()
        snaps = emp_store.snapshots()

    summary_map = {s.employer.employer_key: s for s in summaries_list}

    # 2. Load candidate opportunities read-only
    opps = []
    for lpath in ledger_paths:
        opps.extend(read_opportunities(lpath))
    for md_path in status_md or ():
        opps.extend(read_status_opportunities(md_path))

    # 3. Load applied and excluded companies
    applied: set[str] = set()
    if tracker_path.exists():
        for row in sources.read_tracker(tracker_path):
            if str(row.get("status") or "").lower() in {"submitted", "applied"}:
                k = normalize_employer_key(str(row.get("company") or ""))
                if k:
                    applied.add(k)

    excluded: set[str] = set()
    if settings_path.exists():
        try:
            loaded_settings, _ = TomlSettings(settings_path).load()
            for comp in loaded_settings.rules.companies.exclude:
                k = normalize_employer_key(comp)
                if k:
                    excluded.add(k)
        except (OSError, SettingsError):
            pass

    # Signature and location come from the private profile at runtime, never from source.
    private_profile = _read_private_profile(
        applicant_profile or DATA_DIR / "profile.json"
    )
    queue_items = build_action_queue(
        opps,
        summary_map,
        applied_companies=frozenset(applied),
        excluded_companies=frozenset(excluded),
        sender=_outreach_sender(private_profile),
    )

    # 4. Write standing-policy.json (no street address, phone, email, or clearance).
    # Outreach drafts in action-queue.json do carry the profile's signature.
    current_city_state = ", ".join(
        p
        for p in (_text(private_profile, "city"), _text(private_profile, "state"))
        if p
    )
    relocation_note = (
        "open to relocation"
        if private_profile.get("open_to_relocation") is True
        else "not open to relocation"
    )
    if profile_json and profile_json.exists():
        try:
            prof = json.loads(profile_json.read_text(encoding="utf-8"))
            pol = (prof.get("location") or {}).get("policy") or {}
            if pol.get("open_to"):
                relocation_note = str(pol["open_to"])
        except (OSError, ValueError):
            pass

    standing_policy = {
        "generated_at": utc_now(),
        "rules": {
            "work_authorization": {
                "authorized_to_work_us": True,
                "requires_sponsorship": False,
                "citizenship": "U.S. citizen",
            },
            "location_schedule": {
                "current_city_state": current_city_state,
                "policy": relocation_note,
                "open_to_nights_weekends_holidays": True,
                "max_travel_percent": 80,
                "employer_paid_relocation_required": False,
            },
            "compensation": {
                "floor_usd": None,
                "free_text": "Negotiable based on the role and total compensation",
                "numeric_when_forced": "posted_range_midpoint_only_else_hold",
            },
            "availability": {
                "earliest_start": "Approx. 2 weeks / immediate to confirm",
            },
            "ai_policy": {
                "disclose_ai_workflow_work_since": "2024-12",
                "never_fabricate_years_or_narrative": True,
            },
            "privacy_legal_consent": {
                "auto_accept_standard_applicant_privacy_ack": True,
                "hold_separate_nda_or_claims_release_or_marketing": True,
            },
            "eeo_self_identification": {
                "veteran": "I identify as a protected veteran",
                "gender": "Prefer not to say",
                "race_ethnicity": "Decline to answer",
                "disability": "per_form_prompt",
            },
            "rate_pacing": {
                "max_roles_per_company": 1,
                "exclude_sales_sdr_bdr": True,
                "exclude_senior_staff_principal_manager": True,
            },
        },
        "manual_hold_triggers": [
            "nyc_street_address_and_zip_unconfirmed",
            "years_of_experience_blank_by_rule",
            "disability_per_form_choice",
            "never_surface_clearance_specifics",
            "interactive_captcha",
            "audio_or_video_recording",
        ],
    }
    _write_private_export(
        target_dir / "standing-policy.json",
        json.dumps(standing_policy, indent=2, ensure_ascii=False) + "\n",
    )

    # 5. Write action-queue.json & ACTION-QUEUE.md
    auto_items = [i for i in queue_items if i.route is QueueRoute.AUTO_APPLY_READY]
    bypass_items = [i for i in queue_items if i.route is QueueRoute.RECRUITER_BYPASS]
    dead_items = [i for i in queue_items if i.route is QueueRoute.GATED_DEAD_LETTER]

    queue_payload = {
        "generated_at": utc_now(),
        "counts": {
            "auto_apply_ready": len(auto_items),
            "recruiter_bypass": len(bypass_items),
            "gated_dead_letter": len(dead_items),
        },
        "recruiter_bypass": [
            {
                "employer_key": i.employer_key,
                "company": i.company,
                "title": i.title,
                "url": i.url,
                "location": i.location,
                "provider": i.provider,
                "lane": i.lane.value,
                "resume_variant": i.resume_variant,
                "gates": [g.value for g in i.gates],
                "to_contacts": [
                    {
                        "name": c.display_name,
                        "email": c.email,
                        "channel": c.channel_kind.value,
                    }
                    for c in i.replyable_contacts
                ],
                "outreach_subject": i.outreach_subject,
                "outreach_body": i.outreach_body,
            }
            for i in bypass_items
        ],
        "auto_apply_ready": [
            {
                "employer_key": i.employer_key,
                "company": i.company,
                "title": i.title,
                "url": i.url,
                "location": i.location,
                "provider": i.provider,
                "lane": i.lane.value,
                "score": i.score,
                "resume_variant": i.resume_variant,
                "friction_tier": i.friction_tier.value,
                "needs_manual_fields": list(i.needs_manual_fields),
            }
            for i in auto_items
        ],
    }
    _write_private_export(
        target_dir / "action-queue.json",
        json.dumps(queue_payload, indent=2, ensure_ascii=False) + "\n",
    )

    q_lines = [
        "# Autonomous Action Queue & Warm-HR Bypass Packets",
        "",
        f"Generated: {utc_now()} | Auto-Apply Ready: **{len(auto_items)}** | Warm-HR Bypass: **{len(bypass_items)}** | Gated Dead-Letter: **{len(dead_items)}**",
        "",
        "## 1. Warm / Direct HR Bypass Packets (Portal-Blocked High-Fit Roles)",
        "",
    ]
    for i in bypass_items:
        to_str = ", ".join(
            f"{c.display_name} <{c.email}>" if c.display_name else c.email
            for c in i.replyable_contacts[:3]
        )
        gates_str = ", ".join(g.value for g in i.gates) or "gated"
        q_lines.extend(
            [
                f"### {i.company} — {i.title}",
                f"- **Lane:** `{i.lane.value}` | **Resume:** `{i.resume_variant}` | **Gates:** `{gates_str}`",
                f"- **To:** {to_str}",
                f"- **Role URL:** {i.url}",
                f"- **Subject:** `{i.outreach_subject}`",
                "",
                "```text",
                i.outreach_body,
                "```",
                "",
            ]
        )

    q_lines.extend(
        [
            "## 2. Auto-Apply Ready Queue (1 Best Role per Company)",
            "",
            "| Score | Lane | Company | Role | ATS | Tier | Resume | Manual Holds |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for i in auto_items[:80]:
        holds = ", ".join(i.needs_manual_fields) or "none"
        q_lines.append(
            f"| {i.score} | `{i.lane.value}` | {i.company} | [{i.title}]({i.url}) | {i.provider or '—'} | `{i.friction_tier.value}` | `{i.resume_variant}` | {holds} |"
        )
    _write_private_export(target_dir / "ACTION-QUEUE.md", "\n".join(q_lines) + "\n")

    # 6. Write LINKEDIN-PART2-BRIEF.md from live employers.db numbers
    tier_counts = Counter(s.friction_tier.value for s in summaries_list)
    gate_counts = Counter(
        g.value for s in summaries_list for g in s.gates if g is not GateKind.NONE
    )
    replyable_cnt = sum(1 for c in contacts if c.is_replyable)
    ok_snaps = sum(1 for s in snaps if s.status == "ok")
    beyond_counts = Counter(
        o.category.value
        for o in obs
        if o.is_beyond_standard and o.requiredness != "optional"
    )
    b_lines = [
        "# LinkedIn Part 2 Data Brief: Mapping Pre-Submit Friction Across 1,200+ Employers",
        "",
        f"Generated: {utc_now()} | Grounded strictly in local `employers.db` and live public ATS snapshots.",
        "",
        "## Headline Dataset Metrics",
        "",
        f"- **Tracked employers:** {len(summaries_list)}",
        f"- **Live `ok` ATS form-schema snapshots:** {ok_snaps}",
        f"- **Total pre-submit requirement observations:** {len(obs)}",
        f"- **HR / ATS sender records:** {len(contacts)} (`{replyable_cnt}` replyable human/team contacts)",
        f"- **Friction split:** `{tier_counts.get('low_friction', 0)}` low-friction, `{tier_counts.get('custom_questions', 0)}` custom-questions, `{tier_counts.get('gated', 0)}` gated",
        "",
        "## Blocking Gate Breakdown (Unique Companies)",
        "",
        "| Gate | Companies |",
        "| --- | ---: |",
    ]
    for g_name, g_cnt in gate_counts.most_common():
        b_lines.append(f"| `{g_name}` | {g_cnt} |")
    b_lines.extend(
        [
            "",
            "## Top Beyond-Standard Required Ask Categories",
            "",
            "| Category | Observations |",
            "| --- | ---: |",
        ]
    )
    for cat_name, cnt in beyond_counts.most_common(12):
        b_lines.append(f"| `{cat_name}` | {cnt} |")
    _write_private_export(
        target_dir / "LINKEDIN-PART2-BRIEF.md", "\n".join(b_lines) + "\n"
    )

    console.print(
        f"[green]Queue planned: auto_apply_ready={len(auto_items)}, "
        f"recruiter_bypass={len(bypass_items)}, gated_dead_letter={len(dead_items)} -> {target_dir}[/green]"
    )
