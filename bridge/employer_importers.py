"""Read-only importers from JobPilot stores, campaign artifacts, and Gmail metadata."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from jobpilot.bridge import sources
from jobpilot.bridge.timestamps import to_utc
from jobpilot.engine.adapters.ats_forms import parse_greenhouse_form
from jobpilot.engine.adapters.toml_settings import SETTINGS_FILENAME, TomlSettings
from jobpilot.engine.domain import (
    PUBLIC_API_PROVIDERS,
    EmployerRecord,
    FormSnapshot,
    GateKind,
    Lane,
    Opportunity,
    RequirementCategory,
    RequirementObservation,
    SettingsError,
    build_role_key,
    classify_requirement,
    extract_hr_contact,
    infer_ats_provider_and_gate,
    normalize_employer_key,
    redact_sensitive_text,
    utc_now,
)
from jobpilot.engine.ports import EmployerStore


@dataclass
class EmployerImportReport:
    """Summary counts and warnings from importing employer intelligence sources."""

    sources_read: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    employers_upserted: int = 0
    contacts_counted: int = 0
    observations_added: int = 0
    snapshots_recorded: int = 0


def read_opportunities(path: Path) -> list[Opportunity]:
    """Read opportunities from a SQLite ledger strictly in read-only URI mode."""
    if not path.exists():
        return []
    try:
        with closing(
            sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        ) as db:
            db.row_factory = sqlite3.Row
            has_table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'opportunities'"
            ).fetchone()
            if not has_table:
                return []
            rows = db.execute(
                "SELECT id, identity_key, lane, company, title, url, location, "
                "provider, tenant, provider_job_id, first_seen, last_seen "
                "FROM opportunities ORDER BY last_seen DESC, id"
            ).fetchall()
    except sqlite3.Error:
        return []

    out: list[Opportunity] = []
    for row in rows:
        try:
            lane = Lane(str(row["lane"] or "job"))
        except ValueError:
            lane = Lane.JOB
        out.append(
            Opportunity(
                id=str(row["id"] or ""),
                identity_key=str(row["identity_key"] or ""),
                lane=lane,
                company=str(row["company"] or ""),
                title=str(row["title"] or ""),
                url=str(row["url"] or ""),
                location=str(row["location"] or ""),
                provider=str(row["provider"] or ""),
                tenant=str(row["tenant"] or ""),
                provider_job_id=str(row["provider_job_id"] or ""),
                first_seen=str(row["first_seen"] or ""),
                last_seen=str(row["last_seen"] or ""),
            )
        )
    return out


def resolve_candidate_emails(
    data_dir: Path | None, extra: Iterable[str] = ()
) -> frozenset[str]:
    """Collect known applicant email addresses so they are never indexed as HR contacts."""
    emails: set[str] = {
        e.strip().lower() for e in extra if isinstance(e, str) and "@" in e
    }
    if data_dir is None:
        return frozenset(emails)

    toml_path = data_dir / SETTINGS_FILENAME
    if toml_path.exists():
        try:
            settings, _ = TomlSettings(toml_path).load()
            if settings.profile.email and "@" in settings.profile.email:
                emails.add(settings.profile.email.strip().lower())
        except (OSError, SettingsError):
            pass

    prof_path = data_dir / "profile.json"
    if prof_path.exists():
        try:
            prof = json.loads(prof_path.read_text(encoding="utf-8"))
            if isinstance(prof, dict):
                val = str(prof.get("email") or "").strip().lower()
                if "@" in val:
                    emails.add(val)
        except (OSError, ValueError):
            pass

    return frozenset(emails)


def _parse_datetime(raw: Any) -> str:
    val = str(raw or "").strip()
    if not val:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
        return f"{val}T00:00:00+00:00"
    try:
        iso, _ = to_utc(val, "")
        return iso
    except Exception:
        pass
    try:
        cleaned = re.sub(r"\s*\([A-Z]+\)\s*$", "", val)
        dt = parsedate_to_datetime(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat(timespec="seconds")
    except Exception:
        return ""


def _read_json_optional(path: Path, report: EmployerImportReport) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        report.warnings.append(f"Skipped unreadable JSON {path.name}: {exc}")
        return None


def _ensure_employer(
    store: EmployerStore,
    report: EmployerImportReport,
    *,
    name: str,
    provider: str = "",
    token: str = "",
    domain: str = "",
    seen_at: str = "",
) -> str:
    key = normalize_employer_key(name)
    if not key:
        return ""
    board_tokens: tuple[str, ...] = ()
    if provider and token and provider in PUBLIC_API_PROVIDERS:
        board_tokens = (f"{provider}:{token.lower()}",)
    store.upsert_employer(
        EmployerRecord(
            employer_key=key,
            display_name=(name or key).strip(),
            providers=(provider,) if provider else (),
            board_tokens=board_tokens,
            domains=(domain.lower(),) if domain else (),
            first_seen=seen_at,
            last_seen=seen_at,
        )
    )
    report.employers_upserted += 1
    return key


def _add_obs(
    store: EmployerStore, report: EmployerImportReport, obs: RequirementObservation
) -> None:
    if store.record_observation(obs):
        report.observations_added += 1


def _build_alias_map(store: EmployerStore) -> dict[str, str]:
    """Build unambiguous alias -> canonical employer_key map from known employers."""
    candidates: dict[str, set[str]] = {}
    for emp in store.employers():
        candidates.setdefault(emp.employer_key, set()).add(emp.employer_key)
        short = re.sub(
            r"\s+(?:ai|labs|health|technologies|technology|software|systems|solutions|group)$",
            "",
            emp.employer_key,
        ).strip()
        if short and len(short) >= 3:
            candidates.setdefault(short, set()).add(emp.employer_key)
        for bt in emp.board_tokens:
            if ":" in bt:
                tok = bt.split(":", 1)[1].strip().lower()
                if tok and len(tok) >= 3:
                    candidates.setdefault(tok, set()).add(emp.employer_key)
        for dom in emp.domains:
            if dom:
                candidates.setdefault(dom.lower(), set()).add(emp.employer_key)
                base = dom.split(".")[0].lower()
                if len(base) >= 3:
                    candidates.setdefault(base, set()).add(emp.employer_key)
    return {k: next(iter(v)) for k, v in candidates.items() if len(v) == 1}


def _import_portals(
    path: Path, store: EmployerStore, report: EmployerImportReport
) -> None:
    data = _read_json_optional(path, report)
    if not isinstance(data, list):
        return
    report.sources_read.append(str(path))
    for item in data:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("portal") or "").strip().lower()
        token = str(item.get("value") or "").strip()
        label = str(item.get("label") or token).strip()
        if not label:
            continue
        _ensure_employer(store, report, name=label, provider=provider, token=token)


def _import_tracker(
    path: Path, store: EmployerStore, report: EmployerImportReport
) -> None:
    if not path.exists():
        return
    try:
        rows = sources.read_tracker(path)
    except (OSError, sqlite3.Error) as exc:
        report.warnings.append(f"Skipped unreadable tracker {path.name}: {exc}")
        return
    report.sources_read.append(str(path))
    for row in rows:
        company = str(row.get("company") or "").strip()
        url = str(row.get("job_url") or "").strip()
        prov, tenant, _, default_gate = infer_ats_provider_and_gate(url)
        seen = _parse_datetime(row.get("updated_at") or row.get("applied_at"))
        key = _ensure_employer(
            store, report, name=company, provider=prov, token=tenant, seen_at=seen
        )
        if not key or not default_gate:
            continue
        title = str(row.get("job_title") or "").strip()
        rkey = build_role_key(
            employer_key=key,
            role_title=title,
            role_url=url,
            provider=prov,
            source_kind="tracker_import",
        )
        _add_obs(
            store,
            report,
            RequirementObservation(
                employer_key=key,
                employer_name=company,
                role_key=rkey,
                role_title=title,
                role_url=url,
                provider=prov,
                category=(
                    RequirementCategory.OTHER
                    if default_gate is GateKind.ACCOUNT_REQUIRED
                    else RequirementCategory.PRIVACY_LEGAL_CONSENT
                ),
                label=f"{prov} portal gate ({default_gate.value})",
                field_type="portal_gate",
                requiredness="required",
                gate_kind=default_gate,
                is_beyond_standard=True,
                source_kind="tracker_import",
                source_ref=f"applications:{row.get('id', '')}",
                observed_at=seen,
            ),
        )


def _import_opportunities_file(
    path: Path, store: EmployerStore, report: EmployerImportReport
) -> None:
    if not path.exists():
        return
    opps = read_opportunities(path)
    if not opps:
        return
    report.sources_read.append(str(path))
    for opp in opps:
        prov, tenant, _, _ = infer_ats_provider_and_gate(
            opp.url, opp.provider, opp.tenant, opp.provider_job_id
        )
        _ensure_employer(
            store,
            report,
            name=opp.company,
            provider=prov,
            token=tenant,
            seen_at=opp.last_seen or opp.first_seen,
        )


def _import_employer_asks(
    path: Path, store: EmployerStore, report: EmployerImportReport
) -> None:
    data = _read_json_optional(path, report)
    if not isinstance(data, dict):
        return
    items = data.get("observations")
    if not isinstance(items, list):
        return
    report.sources_read.append(str(path))
    for row in items:
        if not isinstance(row, dict):
            continue
        company = str(row.get("employer") or "").strip()
        url = str(row.get("source_url") or "").strip()
        prov, tenant, _, _ = infer_ats_provider_and_gate(url)
        seen = _parse_datetime(row.get("observed_date"))
        key = _ensure_employer(
            store, report, name=company, provider=prov, token=tenant, seen_at=seen
        )
        if not key:
            continue
        label = str(row.get("ask_or_condition") or "").strip()
        req = str(row.get("requiredness") or "unknown").strip().lower()
        hint = str(row.get("ask_category") or "").strip().lower()
        cat, gate, beyond = classify_requirement(label, "", req, category_hint=hint)
        title = str(row.get("role") or "").strip()
        rkey = build_role_key(
            employer_key=key,
            role_title=title,
            role_url=url,
            provider=prov,
            source_kind="employer_asks",
        )
        _add_obs(
            store,
            report,
            RequirementObservation(
                employer_key=key,
                employer_name=company,
                role_key=rkey,
                role_title=title,
                role_url=url,
                provider=prov,
                category=cat,
                label=label,
                requiredness=req,
                gate_kind=gate,
                is_beyond_standard=beyond,
                source_kind="employer_asks",
                source_ref=str(
                    row.get("observation_id") or row.get("source_ref") or ""
                ),
                observed_at=seen,
            ),
        )


def _import_fourth_gh_forms(
    path: Path, store: EmployerStore, report: EmployerImportReport
) -> None:
    data = _read_json_optional(path, report)
    if not isinstance(data, dict):
        return
    records = data.get("records")
    if not isinstance(records, list):
        return
    report.sources_read.append(str(path))
    fetched_at = _parse_datetime(data.get("fetched_at")) or utc_now()
    for rec in records:
        if not isinstance(rec, dict):
            continue
        board = str(rec.get("board") or "").strip()
        job_id = str(rec.get("id") or rec.get("job_id") or "").strip()
        title = str(rec.get("title") or "").strip()
        url = str(rec.get("absolute_url") or "").strip()
        if not board:
            continue
        key = _ensure_employer(
            store,
            report,
            name=board,
            provider="greenhouse",
            token=board,
            seen_at=fetched_at,
        )
        if not key:
            continue
        rkey = build_role_key(
            employer_key=key,
            role_title=title,
            role_url=url,
            provider="greenhouse",
            tenant=board,
            provider_job_id=job_id,
            source_kind="live_ats_form",
        )
        snap, obs_list = parse_greenhouse_form(
            rec,
            employer_key=key,
            employer_name=board,
            role_key=rkey,
            role_title=title,
            role_url=url,
            fetched_at=fetched_at,
        )
        store.record_snapshot(snap)
        report.snapshots_recorded += 1
        for obs in obs_list:
            _add_obs(store, report, obs)


def _import_holds_and_receipts(
    holds_path: Path,
    receipts_path: Path,
    store: EmployerStore,
    report: EmployerImportReport,
) -> None:
    holds = _read_json_optional(holds_path, report)
    if isinstance(holds, dict) and isinstance(holds.get("items"), list):
        report.sources_read.append(str(holds_path))
        seen = _parse_datetime(holds.get("updated_at"))
        for item in holds["items"]:
            if not isinstance(item, dict):
                continue
            company = str(item.get("company") or "").strip()
            title = str(item.get("title") or "").strip()
            status = str(item.get("status") or "").strip().lower()
            reason = str(item.get("reason") or "").strip()
            key = _ensure_employer(store, report, name=company, seen_at=seen)
            if not key:
                continue
            rkey = build_role_key(
                employer_key=key, role_title=title, source_kind="campaign_hold"
            )
            cat, gate, beyond = classify_requirement(
                f"{status} {reason}", "", "required"
            )
            if gate is None and ("unconfirmed" in status or "error" in status):
                gate = GateKind.SILENT_DROP_OR_RATE_LIMIT
            if gate or beyond:
                _add_obs(
                    store,
                    report,
                    RequirementObservation(
                        employer_key=key,
                        employer_name=company,
                        role_key=rkey,
                        role_title=title,
                        category=cat,
                        label=redact_sensitive_text(reason[:220] or status),
                        field_type="campaign_hold",
                        requiredness="required",
                        gate_kind=gate,
                        is_beyond_standard=True,
                        source_kind="campaign_hold",
                        source_ref=str(item.get("id") or ""),
                        observed_at=seen,
                    ),
                )

    receipts = _read_json_optional(receipts_path, report)
    if isinstance(receipts, list):
        report.sources_read.append(str(receipts_path))
        for item in receipts:
            if not isinstance(item, dict):
                continue
            company = str(item.get("company") or "").strip()
            url = str(item.get("url") or "").strip()
            prov, tenant, _, _ = infer_ats_provider_and_gate(url)
            seen = _parse_datetime(
                item.get("submitted_at") or item.get("receipt_received_at")
            )
            _ensure_employer(
                store, report, name=company, provider=prov, token=tenant, seen_at=seen
            )


def _import_history_evidence(
    path: Path,
    store: EmployerStore,
    report: EmployerImportReport,
    candidate_emails: frozenset[str],
) -> None:
    data = _read_json_optional(path, report)
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        return
    report.sources_read.append(str(path))
    for rec in data["records"]:
        if not isinstance(rec, dict):
            continue
        company = str(rec.get("company") or "").strip()
        if not company or company.lower() in candidate_emails or "@" in company:
            continue
        url = str(rec.get("url") or "").strip()
        prov, tenant, _, _ = infer_ats_provider_and_gate(url)
        seen = _parse_datetime(rec.get("date"))
        _ensure_employer(
            store, report, name=company, provider=prov, token=tenant, seen_at=seen
        )


def _import_preflight_list(
    path: Path, store: EmployerStore, report: EmployerImportReport, source_tag: str
) -> None:
    data = _read_json_optional(path, report)
    if not isinstance(data, list):
        return
    report.sources_read.append(str(path))
    for item in data:
        if not isinstance(item, dict):
            continue
        company = str(item.get("company") or "").strip()
        title = str(item.get("title") or "").strip()
        url = str(item.get("apply_url") or item.get("url") or "").strip()
        prov_decl = str(item.get("provider") or "").strip().lower()
        board = str(item.get("board") or "").strip()
        post_id = str(item.get("posting_id") or "").strip()
        prov, tenant, job_id, default_gate = infer_ats_provider_and_gate(
            url, prov_decl, board, post_id
        )
        key = _ensure_employer(store, report, name=company, provider=prov, token=tenant)
        if not key:
            continue
        rkey = build_role_key(
            employer_key=key,
            role_title=title,
            role_url=url,
            provider=prov,
            tenant=tenant,
            provider_job_id=job_id,
            source_kind=source_tag,
        )

        gates_seen: set[GateKind] = {default_gate} if default_gate else set()
        obs_for_role: list[RequirementObservation] = []

        for q in item.get("required") or []:
            if not isinstance(q, dict):
                continue
            label = str(q.get("label") or q.get("name") or "").strip()
            if not label:
                continue
            types = q.get("types")
            ftype = (
                str(types[0]).strip()
                if isinstance(types, list) and types
                else str(q.get("type") or "").strip()
            )
            cat, gate, beyond = classify_requirement(label, ftype, "required")
            if gate:
                gates_seen.add(gate)
            obs_for_role.append(
                RequirementObservation(
                    employer_key=key,
                    employer_name=company,
                    role_key=rkey,
                    role_title=title,
                    role_url=url,
                    provider=prov,
                    category=cat,
                    label=label,
                    field_type=ftype,
                    requiredness="required",
                    gate_kind=gate,
                    is_beyond_standard=beyond,
                    source_kind=source_tag,
                    source_ref=str(item.get("campaign_id") or item.get("id") or ""),
                )
            )

        for key_name in (
            "possible_legal_or_policy_questions",
            "possible_additional_information",
            "flags",
        ):
            for extra in item.get(key_name) or []:
                text = (
                    str(extra.get("label") or extra.get("message") or extra)
                    if isinstance(extra, dict)
                    else str(extra)
                ).strip()
                if not text:
                    continue
                cat, gate, beyond = classify_requirement(text, "flag", "required")
                if gate:
                    gates_seen.add(gate)
                obs_for_role.append(
                    RequirementObservation(
                        employer_key=key,
                        employer_name=company,
                        role_key=rkey,
                        role_title=title,
                        role_url=url,
                        provider=prov,
                        category=cat,
                        label=text[:220],
                        field_type="flag",
                        requiredness="required",
                        gate_kind=gate,
                        is_beyond_standard=beyond,
                        source_kind=source_tag,
                        source_ref=key_name,
                    )
                )

        readiness = str(item.get("readiness") or "").strip()
        if readiness:
            cat, gate, beyond = classify_requirement(readiness, "readiness", "unknown")
            if gate:
                gates_seen.add(gate)
            if gate or beyond:
                obs_for_role.append(
                    RequirementObservation(
                        employer_key=key,
                        employer_name=company,
                        role_key=rkey,
                        role_title=title,
                        role_url=url,
                        provider=prov,
                        category=cat,
                        label=readiness[:220],
                        field_type="readiness",
                        requiredness="unknown",
                        gate_kind=gate,
                        is_beyond_standard=beyond,
                        source_kind=source_tag,
                        source_ref=str(item.get("campaign_id") or ""),
                    )
                )

        if default_gate and default_gate not in {o.gate_kind for o in obs_for_role}:
            obs_for_role.append(
                RequirementObservation(
                    employer_key=key,
                    employer_name=company,
                    role_key=rkey,
                    role_title=title,
                    role_url=url,
                    provider=prov,
                    category=(
                        RequirementCategory.OTHER
                        if default_gate is GateKind.ACCOUNT_REQUIRED
                        else RequirementCategory.PRIVACY_LEGAL_CONSENT
                    ),
                    label=f"{prov} portal gate ({default_gate.value})",
                    field_type="portal_gate",
                    requiredness="required",
                    gate_kind=default_gate,
                    is_beyond_standard=True,
                    source_kind=source_tag,
                    source_ref=f"inferred:{prov}",
                )
            )

        for obs in obs_for_role:
            _add_obs(store, report, obs)

        if obs_for_role or default_gate:
            beyond_cnt = sum(1 for o in obs_for_role if o.is_beyond_standard)
            snap_status = (
                "ok" if (item.get("required") and not default_gate) else "inferred_gate"
            )
            store.record_snapshot(
                FormSnapshot(
                    role_key=rkey,
                    employer_key=key,
                    provider=prov,
                    role_title=title,
                    role_url=url,
                    status=snap_status,
                    field_count=len(obs_for_role),
                    beyond_standard_count=beyond_cnt,
                    gates=tuple(sorted(gates_seen, key=lambda g: g.value)),
                )
            )
            report.snapshots_recorded += 1


_MD_LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)$")


def _import_status_markdown(
    path: Path, store: EmployerStore, report: EmployerImportReport, source_tag: str
) -> None:
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.warnings.append(f"Skipped unreadable markdown {path.name}: {exc}")
        return
    report.sources_read.append(str(path))
    for line in text.splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if cells[0].lower() in {"#", "employer"}:
            continue
        url = ""
        if len(cells) >= 4:
            # | # | [Employer · Role](url) | Outcome | Evidence |
            m = _MD_LINK_RE.match(cells[1])
            label_text = m.group(1) if m else cells[1]
            url = m.group(2) if m else ""
            parts = [p.strip() for p in label_text.split("·", 1)]
            company = parts[0]
            title = parts[1] if len(parts) > 1 else ""
            outcome = cells[2]
            evidence = cells[3]
        else:
            # | Employer | Role/State | Evidence/Reason |
            company = cells[0]
            title = cells[1]
            outcome = cells[1]
            evidence = cells[2]

        if not company:
            continue
        prov, tenant, _, default_gate = infer_ats_provider_and_gate(url)
        key = _ensure_employer(store, report, name=company, provider=prov, token=tenant)
        if not key:
            continue

        out_low = outcome.lower()
        ev_low = evidence.lower()
        gate: GateKind | None = None
        if (
            "verification required" in out_low
            or "security code" in ev_low
            or "8-character" in ev_low
            or "eight-character" in ev_low
        ):
            gate = GateKind.EMAIL_VERIFICATION_CODE
        elif (
            "attempted without receipt" in out_low
            or "attempted, no receipt" in out_low
            or "submission_outcome_unconfirmed" in out_low
            or "submission_error_unconfirmed" in out_low
            or "rate-limit" in ev_low
        ):
            gate = GateKind.SILENT_DROP_OR_RATE_LIMIT
        elif (
            "captcha" in ev_low
            or "human check" in ev_low
            or "human verification" in ev_low
            or "not a robot" in ev_low
            or "datadome" in ev_low
        ):
            gate = GateKind.CAPTCHA
        elif "application-limit" in ev_low or "application limit" in ev_low:
            gate = GateKind.APPLICATION_LIMIT
        elif (
            "workday" in ev_low
            or "sign in" in ev_low
            or "sign-in" in ev_low
            or "create account" in ev_low
            or "create an account" in ev_low
            or default_gate is GateKind.ACCOUNT_REQUIRED
        ):
            gate = GateKind.ACCOUNT_REQUIRED
        elif (
            "consent" in ev_low
            or "privacy" in ev_low
            or "terms of service" in ev_low
            or "export-control" in ev_low
            or "investigation" in ev_low
            or default_gate is GateKind.CONSENT_WALL
        ):
            gate = GateKind.CONSENT_WALL
        elif out_low.startswith("confirmed"):
            gate = GateKind.NONE

        rkey = build_role_key(
            employer_key=key,
            role_title=title,
            role_url=url,
            provider=prov,
            source_kind=source_tag,
        )
        cat, cls_gate, beyond = classify_requirement(
            f"{outcome} {evidence}", "status", "required"
        )
        eff_gate = gate if gate is not None else cls_gate
        if eff_gate is not None or (out_low == "blocked" and evidence):
            _add_obs(
                store,
                report,
                RequirementObservation(
                    employer_key=key,
                    employer_name=company,
                    role_key=rkey,
                    role_title=title,
                    role_url=url,
                    provider=prov,
                    category=cat,
                    label=redact_sensitive_text(f"{outcome}: {evidence}"[:220]),
                    field_type="campaign_status",
                    requiredness="required",
                    gate_kind=eff_gate,
                    is_beyond_standard=eff_gate is not GateKind.NONE
                    and (beyond or eff_gate is not None),
                    source_kind=source_tag,
                    source_ref=path.name,
                ),
            )


def _import_mail_indexes(
    analysis_dir: Path,
    store: EmployerStore,
    report: EmployerImportReport,
    candidate_emails: frozenset[str],
    alias_map: Mapping[str, str],
) -> None:
    label_path = analysis_dir / "label-mail-index.json"
    data = _read_json_optional(label_path, report)
    if isinstance(data, list):
        report.sources_read.append(str(label_path))
        for item in data:
            if not isinstance(item, dict):
                continue
            frm = str(item.get("from") or "")
            subj = str(item.get("subject") or "")
            dt = _parse_datetime(item.get("date"))
            msg_id = str(item.get("id") or "")
            contact, gate, emp_key = extract_hr_contact(
                frm,
                subject=subj,
                date_iso=dt,
                candidate_emails=candidate_emails,
                alias_map=alias_map,
            )
            if contact is None or not emp_key:
                continue
            _ensure_employer(
                store,
                report,
                name=emp_key,
                domain=""
                if contact.channel_kind.value == "ats_relay"
                else contact.domain,
                seen_at=dt,
            )
            msg_key = f"label_mail:{msg_id}" if msg_id else ""
            if store.upsert_contact(contact, message_key=msg_key):
                report.contacts_counted += 1
            if gate is not None:
                _add_obs(
                    store,
                    report,
                    RequirementObservation(
                        employer_key=emp_key,
                        employer_name=emp_key,
                        role_key=f"employer:{emp_key}:mail:{gate.value}",
                        category=RequirementCategory.OTHER,
                        label=redact_sensitive_text(subj[:180]),
                        field_type="email_gate",
                        requiredness="required",
                        gate_kind=gate,
                        is_beyond_standard=True,
                        source_kind="mail_index",
                        source_ref=msg_id,
                        observed_at=dt,
                    ),
                )

    comp_path = analysis_dir / "complete-mail-index.json"
    comp_data = _read_json_optional(comp_path, report)
    if isinstance(comp_data, list):
        report.sources_read.append(str(comp_path))
        for item in comp_data:
            if not isinstance(item, dict):
                continue
            subj = str(item.get("subject") or "")
            dt = _parse_datetime(item.get("date"))
            msg_id = str(item.get("id") or "")
            if "security code" not in subj.lower():
                continue
            _, gate, emp_key = extract_hr_contact(
                "no-reply@greenhouse.io",
                subject=subj,
                date_iso=dt,
                candidate_emails=candidate_emails,
                alias_map=alias_map,
            )
            if gate is GateKind.EMAIL_VERIFICATION_CODE and emp_key:
                _ensure_employer(
                    store, report, name=emp_key, provider="greenhouse", seen_at=dt
                )
                _add_obs(
                    store,
                    report,
                    RequirementObservation(
                        employer_key=emp_key,
                        employer_name=emp_key,
                        role_key=f"employer:{emp_key}:mail:email_verification_code",
                        provider="greenhouse",
                        category=RequirementCategory.OTHER,
                        label=redact_sensitive_text(subj[:180]),
                        field_type="email_gate",
                        requiredness="required",
                        gate_kind=GateKind.EMAIL_VERIFICATION_CODE,
                        is_beyond_standard=True,
                        source_kind="mail_index",
                        source_ref=msg_id,
                        observed_at=dt,
                    ),
                )


def import_gmail_jsonl(
    jsonl_path: Path,
    store: EmployerStore,
    report: EmployerImportReport,
    *,
    candidate_emails: frozenset[str] = frozenset(),
) -> None:
    """Import HR sender contacts and verification-code gates from Gmail metadata JSONL.

    Only consumes ``id``, ``internalDate``, and ``From``/``Reply-To``/``Subject``/``Date``
    headers. Explicitly ignores ``To``, ``Cc``, ``Delivered-To``, ``snippet``, and ``body``.
    Raises ``OSError`` or ``ValueError`` if an explicit JSONL path cannot be read.
    """
    if not jsonl_path.exists():
        raise FileNotFoundError(f"Gmail JSONL file not found: {jsonl_path}")
    alias_map = _build_alias_map(store)
    text = jsonl_path.read_text(encoding="utf-8")
    report.sources_read.append(str(jsonl_path))
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ValueError(f"{jsonl_path}:{lineno}: expected JSON object")

        msg_id = str(obj.get("id") or f"line:{lineno}")
        headers_raw = (
            (obj.get("payload") or {}).get("headers")
            if isinstance(obj.get("payload"), dict)
            else None
        )
        if isinstance(headers_raw, list):
            hdrs = {
                str(h.get("name") or "").lower(): str(h.get("value") or "")
                for h in headers_raw
                if isinstance(h, dict)
                and str(h.get("name") or "").lower()
                in {"from", "reply-to", "subject", "date"}
            }
            frm = hdrs.get("from", "")
            reply_to = hdrs.get("reply-to", "")
            subj = hdrs.get("subject", "")
            dt_str = hdrs.get("date", "")
        else:
            frm = str(obj.get("from") or obj.get("From") or "")
            reply_to = str(obj.get("reply_to") or obj.get("Reply-To") or "")
            subj = str(obj.get("subject") or obj.get("Subject") or "")
            dt_str = str(obj.get("date") or obj.get("Date") or "")

        dt_iso = _parse_datetime(dt_str)
        if not dt_iso and obj.get("internalDate"):
            try:
                ts = int(obj["internalDate"]) / 1000.0
                dt_iso = datetime.fromtimestamp(ts, tz=UTC).isoformat(
                    timespec="seconds"
                )
            except (TypeError, ValueError):
                dt_iso = ""

        contact, gate, emp_key = extract_hr_contact(
            frm,
            reply_to_header=reply_to,
            subject=subj,
            date_iso=dt_iso,
            candidate_emails=candidate_emails,
            alias_map=alias_map,
        )
        if contact is None or not emp_key:
            continue

        _ensure_employer(
            store,
            report,
            name=emp_key,
            provider="greenhouse" if gate is GateKind.EMAIL_VERIFICATION_CODE else "",
            domain="" if contact.channel_kind.value == "ats_relay" else contact.domain,
            seen_at=dt_iso,
        )
        if store.upsert_contact(contact, message_key=f"gmail:{msg_id}"):
            report.contacts_counted += 1

        if gate is GateKind.EMAIL_VERIFICATION_CODE:
            _add_obs(
                store,
                report,
                RequirementObservation(
                    employer_key=emp_key,
                    employer_name=emp_key,
                    role_key=f"employer:{emp_key}:gmail:email_verification_code",
                    provider="greenhouse",
                    category=RequirementCategory.OTHER,
                    label="Greenhouse emailed 8-character application security code required",
                    field_type="email_gate",
                    requiredness="required",
                    gate_kind=GateKind.EMAIL_VERIFICATION_CODE,
                    is_beyond_standard=True,
                    source_kind="gmail_metadata",
                    source_ref=msg_id,
                    observed_at=dt_iso,
                ),
            )


def import_all_sources(
    store: EmployerStore,
    *,
    data_dir: Path | None = None,
    job_search_dir: Path | None = None,
    gmail_jsonl: Path | None = None,
    candidate_emails: Iterable[str] = (),
) -> EmployerImportReport:
    """Import JobPilot stores, optional campaign logs, and optional Gmail metadata."""
    report = EmployerImportReport()
    resolved_emails = resolve_candidate_emails(data_dir, candidate_emails)

    if data_dir is not None:
        _import_portals(data_dir / "portals.json", store, report)
        _import_tracker(data_dir / "applications.db", store, report)
        _import_opportunities_file(data_dir / "opportunities.db", store, report)

    if job_search_dir is not None:
        js = Path(job_search_dir)
        five_city = js / "five-city-250-2026-11-03"
        if five_city.is_dir():
            _import_opportunities_file(five_city / "opportunities.db", store, report)
            _import_employer_asks(five_city / "employer-asks.json", store, report)
            _import_fourth_gh_forms(
                five_city / "fourth-gh-form-public-data.json", store, report
            )
            _import_holds_and_receipts(
                five_city / "application-holds.json",
                five_city / "submission-receipts.json",
                store,
                report,
            )
            _import_history_evidence(
                five_city / "history-evidence.json", store, report, resolved_emails
            )
            _import_status_markdown(
                five_city / "STATUS.md", store, report, "five_city_status"
            )

        nyc = js / "nyc-100-2026-11-11"
        if nyc.is_dir():
            _import_preflight_list(
                nyc / "form-preflight.json", store, report, "nyc_preflight"
            )
            _import_preflight_list(
                nyc / "follow-up-form-preflight.json",
                store,
                report,
                "nyc_followup_preflight",
            )
            _import_status_markdown(nyc / "STATUS.md", store, report, "nyc_status")
            _import_status_markdown(
                nyc / "FOLLOW-UP-STATUS.md", store, report, "nyc_followup_status"
            )

        austin = js / "austin-100-2026-11-11"
        if austin.is_dir():
            _import_preflight_list(
                austin / "form-preflight.json", store, report, "austin_preflight"
            )
            _import_status_markdown(
                austin / "STATUS.md", store, report, "austin_status"
            )

        analysis = js / "analysis-2026-09-14"
        if analysis.is_dir():
            alias_map = _build_alias_map(store)
            _import_mail_indexes(analysis, store, report, resolved_emails, alias_map)

    if gmail_jsonl is not None:
        import_gmail_jsonl(
            Path(gmail_jsonl), store, report, candidate_emails=resolved_emails
        )

    return report
