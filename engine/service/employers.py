"""Employer intelligence service: collect public ATS form schemas without mutating opportunities."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from jobpilot.engine.domain import (
    PUBLIC_API_PROVIDERS,
    BoardTarget,
    EmployerRecord,
    Lane,
    Opportunity,
    build_role_key,
    infer_ats_provider_and_gate,
    normalize_employer_key,
)
from jobpilot.engine.ports import Boards, EmployerStore, FormSchemas, Ledger


@dataclass
class CollectReport:
    """Counts and diagnostics from one public ATS form-schema collection pass."""

    inspected: int = 0
    fetched: int = 0
    skipped_existing: int = 0
    inferred_gates: int = 0
    unsupported: int = 0
    errors: int = 0
    observations_added: int = 0
    failures: list[str] = field(default_factory=list)


def sample_board_opportunities(
    boards: Boards,
    targets: Iterable[BoardTarget],
    *,
    per_board: int = 1,
) -> tuple[list[Opportunity], list[str]]:
    """Read sample postings from Boards in memory without writing to opportunities.db."""
    sampled: list[Opportunity] = []
    failed: list[str] = []
    cap = max(1, per_board)
    for target in targets:
        result = boards.fetch(target)
        if result.error:
            failed.append(f"{target.name}: {result.error}")
            continue
        for index, posting in enumerate(result.postings[:cap]):
            listing = posting.listing
            sampled.append(
                Opportunity(
                    id=f"sample:{target.provider}:{target.token.lower()}:{index}",
                    identity_key=listing.identity_key(),
                    lane=listing.lane,
                    company=listing.company,
                    title=listing.title,
                    url=listing.url,
                    location=listing.location,
                    provider=listing.provider,
                    tenant=listing.tenant,
                    provider_job_id=listing.provider_job_id,
                    first_seen=posting.posted_at,
                    last_seen=posting.posted_at,
                )
            )
    return sampled, failed


def _would_fetch_http(opp: Opportunity) -> bool:
    prov, tenant, job_id, _ = infer_ats_provider_and_gate(
        opp.url, opp.provider, opp.tenant, opp.provider_job_id
    )
    return prov in PUBLIC_API_PROVIDERS and bool(tenant and job_id)


def collect_employer_schemas(
    ledger: Ledger | None,
    store: EmployerStore,
    fetcher: FormSchemas,
    *,
    now: str,
    limit: int | None = None,
    provider: str | None = None,
    company: str | None = None,
    refresh_existing: bool = False,
    extra_targets: Iterable[Opportunity] = (),
) -> CollectReport:
    """Collect public ATS form schemas for tracked roles and persist them into EmployerStore."""
    report = CollectReport()
    prov_filter = (provider or "").strip().lower()
    comp_filter = normalize_employer_key(company or "")

    candidates: list[Opportunity] = []
    if ledger is not None:
        candidates.extend(ledger.opportunities(Lane.JOB))
    candidates.extend(extra_targets)

    seen_role_keys: set[str] = set()
    for opp in candidates:
        emp_key = normalize_employer_key(opp.company)
        if not emp_key:
            continue
        if comp_filter and emp_key != comp_filter:
            continue

        inferred_prov, inferred_tenant, inferred_job_id, _ = (
            infer_ats_provider_and_gate(
                opp.url, opp.provider, opp.tenant, opp.provider_job_id
            )
        )
        if prov_filter and inferred_prov != prov_filter:
            continue

        role_key = build_role_key(
            employer_key=emp_key,
            role_title=opp.title,
            role_url=opp.url,
            provider=inferred_prov or opp.provider,
            tenant=inferred_tenant or opp.tenant,
            provider_job_id=inferred_job_id or opp.provider_job_id,
            source_kind="live_ats_form",
        )
        if role_key in seen_role_keys:
            continue
        seen_role_keys.add(role_key)
        report.inspected += 1

        if not refresh_existing and store.has_snapshot(role_key):
            report.skipped_existing += 1
            continue

        is_http = _would_fetch_http(opp)
        if is_http and limit is not None and report.fetched >= limit:
            continue

        board_tokens: tuple[str, ...] = ()
        if inferred_prov in PUBLIC_API_PROVIDERS and inferred_tenant:
            board_tokens = (f"{inferred_prov}:{inferred_tenant}",)
        store.upsert_employer(
            EmployerRecord(
                employer_key=emp_key,
                display_name=opp.company.strip() or emp_key,
                board_tokens=board_tokens,
                providers=(inferred_prov,) if inferred_prov else (),
                first_seen=opp.first_seen or now,
                last_seen=opp.last_seen or now,
            )
        )

        snapshot, observations = fetcher.fetch(opp)
        if is_http and snapshot.status in {"ok", "error", "inferred_gate"}:
            report.fetched += 1

        store.record_snapshot(snapshot)
        if snapshot.status == "ok":
            added = store.replace_observations_for_role(
                snapshot.role_key, "live_ats_form", observations
            )
            report.observations_added += added
        elif snapshot.status == "inferred_gate":
            report.inferred_gates += 1
            for obs in observations:
                if store.record_observation(obs):
                    report.observations_added += 1
        elif snapshot.status == "unsupported":
            report.unsupported += 1
        elif snapshot.status == "error":
            report.errors += 1
            if snapshot.error:
                report.failures.append(f"{opp.company} ({opp.title}): {snapshot.error}")

    return report
