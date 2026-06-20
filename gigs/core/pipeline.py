"""Single source of truth for gig pipeline state.

`pipeline.md` in iCloud is the user-editable record. gigpilot parses it on
each run, surfaces new gigs, and acts on user edits (status changes,
follow-up notes). All other state files (`commands.txt`, `saved_leads.md`,
`applied.jsonl`, `command_results.md`) are deprecated in favor of this.

Schema is one markdown table per file, where each row is a gig. Columns:

  Status | Score | Company — Role | Pay | Apply | Saved | Last touched | Next action | Notes

Each row carries an HTML comment marker `<!-- gig_id:hn-1 -->` after the
final pipe so we can map back to the source gig on subsequent runs.

Status values follow a loose state machine:

  new       — fresh from the latest scan, awaiting decision
  save / s  — user-typed shorthand, normalized to "saved"
  saved     — accepted, will get a follow-up Reminder
  drafted   — proposal written, not sent yet
  sent      — proposal sent, awaiting reply
  replied   — recruiter responded
  interview — interview scheduled
  hired     — closed-won
  pass / p  — user-typed shorthand, normalized to "passed"
  passed    — declined, excluded from future digests
  archived  — same as passed but kept for the record

Anything not in this list is preserved as-is so the user can invent their
own labels (e.g. "ghosted", "withdrawn") without breaking parse.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from jobpilot.gigs.core import store
from jobpilot.gigs.core.dedupe import company_title_key, gig_keys, listing_keys
from jobpilot.gigs.core.io_lock import atomic_write_text, file_lock
from jobpilot.gigs.core.logger import get_logger
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.paths import data_dir, pipeline_dir
from jobpilot.gigs.core.scorer import score_gig
from jobpilot.gigs.core.scrapers.ids import stable_url_suffix

log = get_logger(__name__)

PIPELINE_DIR = pipeline_dir()
PIPELINE_PATH = PIPELINE_DIR / "pipeline.md"

# Stale-`new` rows move here, NOT to an "## Archive" section inside
# pipeline.md: parse_text treats every table with a Status/Score/Company
# header as live rows, so an in-file archive section would resurrect its
# rows on the next parse. A sidecar file is the robust choice.
ARCHIVE_PATH = PIPELINE_DIR / "pipeline_archive.md"
ARCHIVE_AFTER_DAYS = 14

DATA_DIR = data_dir()
STATUS_SNAPSHOT_PATH = DATA_DIR / "pipeline_prev_status.json"
HYGIENE_MARKER = DATA_DIR / ".pipeline_hygiene_done"

# Refuse-to-shrink guard: a write may drop at most this many rows relative
# to what's on disk. Anything bigger means we're about to destroy rows the
# user didn't consciously archive (parse bug, truncated merge, ...).
SHRINK_TOLERANCE = 2

GIG_ID_MARKER_PREFIX = "<!-- gig_id:"
GIG_ID_MARKER_SUFFIX = " -->"

# Legacy (pre-sidecar) reminder bookkeeping: older runs appended this literal
# to the user-facing Notes cell, polluting it and breaking pass-reason
# extraction. Parse strips it (so the next write scrubs the file) and raises
# Row.legacy_reminder_flag so away.sync_reminders_from_pipeline can migrate
# the flag into data/reminder_flags.json.
LEGACY_REMINDER_FLAG = "reminder_created"
_LEGACY_REMINDER_FLAG_RE = re.compile(rf"\s*{LEGACY_REMINDER_FLAG}\s*")

# Legacy gig_id shape from before core/scrapers/ids.py: a salted-hash
# decimal (Python's hash(), up to 19-20 digits). Salted per-process, so
# these IDs can never match anything a scraper mints today. Real external
# IDs (RemoteOK, HN item ids) top out well under 15 digits.
_LEGACY_ID_RE = re.compile(r"^([a-z0-9]+)-(\d{15,})$")

# Statuses that mean "already decided about this gig — do not re-surface"
EXCLUDED_STATUSES = {
    "saved", "drafted", "sent", "replied", "interview", "hired",
    "passed", "archived",
}

HEADER = (
    "Status", "Score", "Company — Role", "Pay", "Apply",
    "Saved", "Last touched", "Next action", "Notes",
)


@dataclass
class Row:
    status: str = "new"
    score: int = 0
    company: str = ""
    role: str = ""
    pay: str = ""
    apply: str = ""
    saved: str = ""
    last_touched: str = ""
    next_action: str = ""
    notes: str = ""
    gig_id: str = ""
    # Set when the parsed Notes cell carried the legacy "reminder_created"
    # literal; the literal itself is stripped at parse time so the next
    # write scrubs it from the user-facing file.
    legacy_reminder_flag: bool = False

    @property
    def excluded_from_future(self) -> bool:
        return self.status in EXCLUDED_STATUSES

    @property
    def is_actively_pursuing(self) -> bool:
        return self.status in {"saved", "drafted", "sent"}

    @property
    def is_replied(self) -> bool:
        return self.status in {"replied", "interview", "hired"}


# ----- normalization -------------------------------------------------------


_STATUS_ALIASES = {
    "": "new",
    "s": "saved",
    "save": "saved",
    "saving": "saved",
    "p": "passed",
    "pass": "passed",
    "skip": "passed",
    "skipped": "passed",
    "x": "passed",
    "no": "passed",
}


def _normalize_status(s: str) -> str:
    key = s.strip().lower()
    return _STATUS_ALIASES.get(key, key) if key in _STATUS_ALIASES else (key or "new")


def _today() -> str:
    return datetime.now().strftime("%-m/%-d")


def parse_last_touched(value: str, today: datetime | None = None) -> datetime | None:
    """Parse M/D dates, picking the most recent calendar day not after today."""
    today = today or datetime.now()
    try:
        month, day = value.split("/")
        m, d = int(month), int(day)
    except (ValueError, AttributeError):
        return None
    end_of_today = today.replace(hour=23, minute=59, second=59)
    candidates: list[datetime] = []
    for year in (today.year, today.year - 1):
        try:
            dt = datetime(year, m, d)
        except ValueError:
            continue
        if dt <= end_of_today:
            candidates.append(dt)
    return max(candidates) if candidates else None


def followups_due(
    rows: list[Row], *, days: int = 3, today: datetime | None = None,
) -> list[Row]:
    """Rows that were 'sent' but not yet replied and have gone quiet for
    `days`+ — most cold-outreach replies come from the 2nd–4th touch, so a
    one-shot sender leaves replies on the table. last_touched-less rows are
    skipped (can't age them)."""
    today = today or datetime.now()
    due: list[Row] = []
    for r in rows:
        if r.status != "sent":
            continue
        dt = parse_last_touched(r.last_touched, today)
        if dt is None:
            continue
        if (today - dt).days >= days:
            due.append(r)
    return due


def _fmt_pay_for_pipeline(g: Gig) -> str:
    if g.salary_max and g.salary_min:
        return f"${g.salary_min/1000:.0f}-${g.salary_max/1000:.0f}K"
    if g.salary_max:
        return f"up to ${g.salary_max/1000:.0f}K"
    if g.pay_hourly_est:
        return f"${g.pay_hourly_est:.0f}/hr"
    return ""


# ----- parse ---------------------------------------------------------------


def parse(path: Path = PIPELINE_PATH) -> list[Row]:
    """Read pipeline.md and return rows. Empty list if the file is missing."""
    if not path.exists():
        return []
    return parse_text(path.read_text())


def parse_text(text: str) -> list[Row]:
    rows: list[Row] = []
    in_table = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.startswith("|"):
            in_table = False
            continue
        # Skip the header row and the separator row
        if "Status" in line and "Score" in line and "Company" in line:
            in_table = True
            continue
        # Separator rows: pipes + dashes only
        if set(line.replace("|", "").replace(" ", "").replace(":", "")) <= {"-"}:
            continue
        if not in_table:
            continue
        rows.append(_parse_row(line))
    return rows


def _parse_row(line: str) -> Row:
    gig_id_match = re.search(
        rf"{re.escape(GIG_ID_MARKER_PREFIX)}\s*([A-Za-z0-9_-]+)\s*{re.escape(GIG_ID_MARKER_SUFFIX.strip())}",
        line,
    )
    gig_id = gig_id_match.group(1) if gig_id_match else ""

    # Strip the trailing comment so it doesn't show up as a fake column
    body = re.sub(
        rf"{re.escape(GIG_ID_MARKER_PREFIX)}.*?{re.escape(GIG_ID_MARKER_SUFFIX.strip())}",
        "",
        line,
    )
    body = body.strip().strip("|")
    parts = [p.strip() for p in body.split("|")]
    while len(parts) < 9:
        parts.append("")

    company = role = ""
    company_role = parts[2]
    if " — " in company_role:
        company, role = company_role.split(" — ", 1)
    else:
        company = company_role

    try:
        score = int(parts[1]) if parts[1] else 0
    except ValueError:
        score = 0

    notes = parts[8]
    legacy_flag = LEGACY_REMINDER_FLAG in notes
    if legacy_flag:
        notes = _LEGACY_REMINDER_FLAG_RE.sub(" ", notes).strip()

    return Row(
        status=_normalize_status(parts[0]),
        score=score,
        company=company.strip(),
        role=role.strip(),
        pay=parts[3],
        apply=parts[4],
        saved=parts[5],
        last_touched=parts[6],
        next_action=parts[7],
        notes=notes,
        gig_id=gig_id,
        legacy_reminder_flag=legacy_flag,
    )


# ----- merge ---------------------------------------------------------------


def row_keys(row: Row) -> set[str]:
    """Every cross-source key this pipeline row can match under (see
    dedupe.listing_keys) — used to recognize reposts of rows we already
    have, whatever fresh ID the source minted for them."""
    return listing_keys(title=row.role, company=row.company, apply_url=row.apply)


def merge_new_gigs(existing: list[Row], ranked: list[Gig]) -> list[Row]:
    """Append fresh gigs from this scan as `new` rows; skip any whose IDs
    already appear in the pipeline (any status) OR that match an existing
    row's cross-source key — reposts arrive with fresh IDs, so ID equality
    alone lets the same role pile up run after run. Returns a new list with
    existing rows preserved verbatim."""
    existing_ids = {r.gig_id for r in existing if r.gig_id}
    existing_keys: set[str] = set()
    for r in existing:
        existing_keys |= row_keys(r)
    out = list(existing)
    for g in ranked:
        if g.id in existing_ids or gig_keys(g) & existing_keys:
            continue
        out.append(Row(
            status="new",
            score=g.fit_score,
            company=g.company or g.source,
            role=(g.title or "").split("|")[0].strip()[:80],
            pay=_fmt_pay_for_pipeline(g),
            apply=g.apply_url or g.url,
            gig_id=g.id,
        ))
        existing_ids.add(g.id)
        existing_keys |= gig_keys(g)
    return out


def _gig_from_row(row: Row) -> Gig:
    """Best-effort Gig reconstruction for re-scoring a row whose listing no
    longer appears in any scan. Description and parsed pay are gone, so
    only the title/company/source layers of the scorer contribute. The
    source is recovered from the gig_id prefix ("wwr-…" → "wwr")."""
    return Gig(
        id=row.gig_id,
        source=row.gig_id.split("-", 1)[0] if row.gig_id else "",
        title=row.role,
        company=row.company,
        url=row.apply,
        apply_url=row.apply,
    )


def rescore_new_rows(rows: list[Row], collected: list[Gig]) -> int:
    """Refresh Score on still-`new` rows with the current scorer, so scores
    track today's calibration instead of staying frozen at whatever the
    scorer said the day the row was minted. Status, Notes, and every other
    user-owned column are untouched.

    Full listing data is used when this scan saw the same gig (matched by
    ID or cross-source key); otherwise the gig is reconstructed from the
    row itself (see _gig_from_row). Mutates rows in place and returns the
    number of rows whose score changed."""
    by_id = {g.id: g for g in collected}
    by_key: dict[str, Gig] = {}
    for g in collected:
        for key in gig_keys(g):
            by_key.setdefault(key, g)

    changed = 0
    for row in rows:
        if row.status != "new":
            continue
        gig = by_id.get(row.gig_id)
        if gig is None:
            for key in row_keys(row):
                gig = by_key.get(key)
                if gig is not None:
                    break
        if gig is None:
            gig = _gig_from_row(row)
        new_score = score_gig(gig).fit_score
        if new_score != row.score:
            row.score = new_score
            changed += 1
    return changed


# ----- one-time hygiene migration -------------------------------------------


def _regenerate_legacy_id(row: Row) -> str:
    """sha256-based replacement for a legacy salted-hash gig_id, derived
    from the row's URL the way core/scrapers/ids.py would. Empty string
    when the row isn't legacy or has no http(s) URL to derive from
    (mailto rows keep their dead ID — harmless, since repost matching is
    by cross-source key, not ID)."""
    match = _LEGACY_ID_RE.match(row.gig_id or "")
    if not match:
        return ""
    url = (row.apply or "").strip()
    if not url.lower().startswith("http"):
        return ""
    return f"{match.group(1)}-{stable_url_suffix(url)}"


def _collapse_keep_rank(row: Row) -> tuple:
    """Which duplicate survives the hygiene collapse. Any user data (a
    decided status or notes) wins; then the most user-touched fields; then
    the most recent Saved/Last-touched date; then a modern sha256 gig_id
    over a legacy salted-hash one (legacy rows predate the ID switch, so
    the modern row is the newer sighting); score breaks the final tie."""
    has_user_data = row.status != "new" or bool(row.notes)
    touched = sum(
        1 for v in (
            row.status != "new", row.notes, row.next_action,
            row.saved, row.last_touched,
        ) if v
    )
    when = (
        parse_last_touched(row.last_touched)
        or parse_last_touched(row.saved)
        or datetime.min
    )
    modern_id = bool(row.gig_id) and not _LEGACY_ID_RE.match(row.gig_id)
    return (has_user_data, touched, when, modern_id, row.score)


def hygiene_migration(rows: list[Row]) -> tuple[list[Row], list[Row], dict[str, str]]:
    """Collapse rows sharing a normalized company+title (the pile-up from
    before merge_new_gigs learned to key-match reposts) and regenerate
    legacy salted-hash gig_ids.

    Returns (kept rows in original order, collapsed-away rows,
    {old_id: new_id} for regenerated IDs). Kept rows are mutated in place
    when their ID is regenerated."""
    groups: dict[str, list[Row]] = {}
    for row in rows:
        if not (row.company or row.role):
            continue  # nothing to key on — never collapse
        groups.setdefault(company_title_key(row.company, row.role), []).append(row)

    removed: list[Row] = []
    removed_idents: set[int] = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        winner = max(group, key=_collapse_keep_rank)
        for row in group:
            if row is not winner:
                removed.append(row)
                removed_idents.add(id(row))
    kept = [r for r in rows if id(r) not in removed_idents]

    remapped: dict[str, str] = {}
    taken = {r.gig_id for r in kept if r.gig_id}
    for row in kept:
        new_id = _regenerate_legacy_id(row)
        if new_id and new_id not in taken:
            remapped[row.gig_id] = new_id
            row.gig_id = new_id
            taken.add(new_id)
    return kept, removed, remapped


def migrate_pipeline_hygiene(
    path: Path = PIPELINE_PATH,
    archive_path: Path = ARCHIVE_PATH,
    marker_path: Path = HYGIENE_MARKER,
) -> dict[str, int]:
    """One-time pipeline cleanup, marker-gated like .applied_migrated:
    collapse duplicate rows into the archive sidecar (IDs retired into
    seen.json so they never resurface) and regenerate legacy gig_ids.
    Returns {"collapsed": n, "ids_regenerated": n}."""
    counts = {"collapsed": 0, "ids_regenerated": 0}
    if marker_path.exists():
        return counts
    rows = parse(path)
    if not rows:
        marker_path.write_text("empty pipeline\n")
        return counts
    kept, removed, remapped = hygiene_migration(rows)
    if removed:
        append_to_archive(removed, archive_path)
        store.mark_archived(sorted(r.gig_id for r in removed if r.gig_id))
    if removed or remapped:
        write(
            kept, path,
            removed_ids={r.gig_id for r in removed if r.gig_id} | set(remapped),
        )
    marker_path.write_text(
        f"collapsed={len(removed)} ids_regenerated={len(remapped)}\n",
    )
    counts["collapsed"] = len(removed)
    counts["ids_regenerated"] = len(remapped)
    return counts


# ----- auto-archive of stale `new` rows --------------------------------------


def split_archivable(
    rows: list[Row],
    first_seen: dict[str, str],
    *,
    now: datetime | None = None,
) -> tuple[list[Row], list[Row]]:
    """Partition rows into (keep, archive). A row is archivable when it is
    still `new` ARCHIVE_AFTER_DAYS after its first sighting — dated by the
    Saved column when set, else its first_seen stamp (store.sync_first_seen
    stamps every undated `new` row at the start of the run, so nothing is
    archived before it has had its full 14 days on the board). Rows with
    any other status, or with no way to date them, always stay."""
    now = now or datetime.now()
    cutoff = now - timedelta(days=ARCHIVE_AFTER_DAYS)
    keep: list[Row] = []
    stale: list[Row] = []
    for row in rows:
        if row.status != "new":
            keep.append(row)
            continue
        born = parse_last_touched(row.saved, now) if row.saved else None
        if born is None and row.gig_id:
            try:
                born = datetime.fromisoformat(first_seen.get(row.gig_id, ""))
            except ValueError:
                born = None
        (stale if born is not None and born < cutoff else keep).append(row)
    return keep, stale


_ARCHIVE_HEADER_LINES = (
    "# GigPilot Pipeline Archive",
    "",
    f"Rows gigpilot moved out of pipeline.md: `new` rows untriaged for "
    f"{ARCHIVE_AFTER_DAYS}+ days, plus duplicates collapsed by the one-time "
    "hygiene migration. This is a sidecar file (not an `## Archive` section "
    "inside pipeline.md) because the pipeline parser treats every "
    "Status/Score/Company table as live rows — an in-file section would "
    "resurrect its rows on the next run.",
    "",
    "Append-only; gigpilot never reads it back, and archived gig IDs are "
    "retired in seen.json so they cannot resurface. To revive a row, move "
    "its line into the table in pipeline.md and set its Status.",
    "",
    f"| {' | '.join(HEADER)} |",
    "|" + "|".join("---" for _ in HEADER) + "|",
)


def append_to_archive(rows: list[Row], path: Path = ARCHIVE_PATH) -> Path:
    """Append rows to the archive sidecar, creating it (with its documented
    header) on first use. Same table schema as pipeline.md so a row can be
    copied back by hand."""
    if not rows:
        return path
    with file_lock(path):
        text = path.read_text() if path.exists() else ""
        if not text.strip():
            text = "\n".join(_ARCHIVE_HEADER_LINES)
        lines = [text.rstrip("\n")]
        lines.extend(_row_line(r) for r in rows)
        atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def stamp_status_changes(
    before: list[Row], after: list[Row], today: str | None = None,
) -> list[Row]:
    """When the user edits a status, stamp Last touched + Saved as needed.
    Pass `before` (the previous run's status snapshot, see
    load_status_snapshot) and `after` (freshly parsed from disk); the
    function returns `after` mutated where status changed."""
    today = today or _today()
    by_id_before = {r.gig_id: r for r in before if r.gig_id}
    for row in after:
        prev = by_id_before.get(row.gig_id)
        if prev is None:
            continue
        if prev.status != row.status:
            row.last_touched = today
            if row.status == "saved" and not row.saved:
                row.saved = today
    return after


# ----- status snapshot (between-run diff base) ------------------------------


def load_status_snapshot(path: Path = STATUS_SNAPSHOT_PATH) -> list[Row]:
    """Statuses as of the END of the previous digest run, as lightweight
    rows ready for stamp_status_changes. Diffing the freshly-parsed pipeline
    against this detects user edits made between runs. Empty list when no
    snapshot exists yet (first run) or the file is unreadable."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    return [
        Row(gig_id=gig_id, status=_normalize_status(str(status)))
        for gig_id, status in data.items()
    ]


def save_status_snapshot(rows: list[Row], path: Path = STATUS_SNAPSHOT_PATH) -> None:
    """Persist {gig_id: status} so the next run can diff against it."""
    snapshot = {r.gig_id: r.status for r in rows if r.gig_id}
    with file_lock(path):
        atomic_write_text(path, json.dumps(snapshot, indent=2))


def _merge_notes(merged: str, disk: str) -> str:
    """Notes column merge: disk wins, except when the digest only appended
    to the on-disk value (disk is a strict prefix of merged — an annotation
    added after this run's first write). Keeping the appended version stops
    the older on-disk copy from stripping it; any other difference is a
    user edit, and the disk value wins."""
    if merged != disk and merged.startswith(disk):
        return merged
    return disk


def _preserve_user_edits(
    merged: list[Row],
    on_disk: list[Row],
    removed_ids: set[str] | frozenset[str] = frozenset(),
    authoritative_status: dict[str, str] | None = None,
) -> list[Row]:
    """Keep phone/Mac edits made while a digest run is in progress.

    Disk wins on the user-editable columns. Saved / Last touched fall back
    to the merged row when blank on disk so fresh status-change stamps
    survive the write; Notes falls back to the merged row when the digest
    only appended to what's on disk (see _merge_notes). Rows that exist
    only on disk (hand-added mid-run, or unknown to this run) are always
    carried over — never dropped — UNLESS their gig_id is in removed_ids:
    those were deliberately retired this run (auto-archived, or collapsed
    by the hygiene migration) and already preserved in the archive sidecar.

    `authoritative_status` lets a deliberate, programmatic status change (the
    phone swiper marking a gig sent/passed) win over disk for those gig_ids —
    otherwise disk-wins would silently revert the swipe to its old status."""
    authoritative_status = authoritative_status or {}
    disk_by_id = {r.gig_id: r for r in on_disk if r.gig_id}
    out: list[Row] = []
    for row in merged:
        disk = disk_by_id.get(row.gig_id)
        if disk is None:
            out.append(row)
            continue
        out.append(Row(
            status=authoritative_status.get(row.gig_id, disk.status),
            score=row.score,
            company=row.company or disk.company,
            role=row.role or disk.role,
            pay=row.pay or disk.pay,
            apply=disk.apply or row.apply,
            saved=disk.saved or row.saved,
            last_touched=disk.last_touched or row.last_touched,
            next_action=disk.next_action,
            notes=_merge_notes(row.notes, disk.notes),
            gig_id=row.gig_id,
            legacy_reminder_flag=row.legacy_reminder_flag or disk.legacy_reminder_flag,
        ))
    merged_ids = {r.gig_id for r in merged if r.gig_id}
    merged_keys = {(r.company, r.role) for r in merged}
    for disk in on_disk:
        if disk.gig_id:
            if disk.gig_id not in merged_ids and disk.gig_id not in removed_ids:
                out.append(disk)
        elif (disk.company, disk.role) not in merged_keys:
            out.append(disk)
    return out


def excluded_ids(rows: list[Row]) -> set[str]:
    """Gig IDs that should be excluded from future digests (anything user
    has already decided about — saved, sent, replied, passed, etc.)."""
    return {r.gig_id for r in rows if r.gig_id and r.excluded_from_future}


# ----- render --------------------------------------------------------------


def render(rows: list[Row]) -> str:
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Sort by status bucket, then score (desc). Row order within a bucket is not
    # preserved across writes — edit Status/Notes on phone; gigpilot merges those
    # fields back before each save.
    def _bucket(r: Row) -> int:
        if r.status == "new":
            return 0
        if r.is_replied:
            return 1
        if r.is_actively_pursuing:
            return 2
        if r.excluded_from_future:
            return 3
        return 4
    rows = sorted(rows, key=lambda r: (_bucket(r), -r.score))

    out = [
        f"# GigPilot Pipeline — refreshed {today}",
        "",
        "Edit any cell from phone or Mac. Save the file when done — gigpilot picks up your changes on the next scheduled run.",
        "",
        "**Status shortcuts you can type:**",
        "",
        "- `s` or `save` → saved (will create a Reminder + appear in weekly summary)",
        "- `p` or `pass` → passed (excluded from future digests forever)",
        "- `drafted` / `sent` / `replied` / `interview` / `hired` — track outcomes",
        "- `p` / `pass` with Notes like `pass:wrong-stack` or `pass:low-pay` — feeds tuning",
        "",
        "Pass reason codes: wrong-stack, low-pay, wrong-role, spam, location, contract-only, duplicate, other",
        "",
        "Empty Status = `new`. Anything not listed above is preserved verbatim.",
        "",
        f"| {' | '.join(HEADER)} |",
        "|" + "|".join("---" for _ in HEADER) + "|",
    ]

    out.extend(_row_line(row) for row in rows)
    out.append("")
    return "\n".join(out)


def _row_line(row: Row) -> str:
    """One markdown table line (shared by pipeline.md and the archive)."""
    cells = [
        row.status,
        str(row.score) if row.score else "",
        (f"{row.company} — {row.role}" if row.company and row.role
            else (row.company or row.role)),
        row.pay,
        row.apply,
        row.saved,
        row.last_touched,
        row.next_action,
        row.notes,
    ]
    cells = [c.replace("|", "/") for c in cells]
    marker = f"{GIG_ID_MARKER_PREFIX}{row.gig_id}{GIG_ID_MARKER_SUFFIX}" if row.gig_id else ""
    return f"| {' | '.join(cells)} | {marker}"


_REFRESHED_LINE_RE = re.compile(
    r"^# GigPilot Pipeline — refreshed .*$", flags=re.MULTILINE,
)


def _content_equal(a: str, b: str) -> bool:
    """Equality modulo the 'refreshed <timestamp>' header line."""
    return _REFRESHED_LINE_RE.sub("", a) == _REFRESHED_LINE_RE.sub("", b)


class WriteResult(NamedTuple):
    """Outcome of write(). `refused` means the shrink guard kept the on-disk
    file untouched — the caller must NOT treat this run's gigs as persisted
    (no mark_seen, no status snapshot), or they vanish forever."""

    path: Path
    refused: bool = False

    def __fspath__(self) -> str:  # os.PathLike, so path-style callers keep working
        return str(self.path)

    def __str__(self) -> str:
        return str(self.path)


def write(
    rows: list[Row],
    path: Path = PIPELINE_PATH,
    *,
    removed_ids: set[str] | frozenset[str] = frozenset(),
    authoritative_status: dict[str, str] | None = None,
) -> WriteResult:
    """Write rows, merging in any on-disk edits first. removed_ids names
    gig_ids deliberately retired this run (auto-archived or collapsed by
    the hygiene migration): they are exempt from the disk-row carry-over
    and raise the shrink guard's allowance accordingly — archiving is a
    deliberate move, not row loss."""
    with file_lock(path):
        on_disk = parse(path) if path.exists() else []
        rows = _preserve_user_edits(
            rows, on_disk, removed_ids=removed_ids,
            authoritative_status=authoritative_status,
        )
        removed_on_disk = sum(
            1 for r in on_disk if r.gig_id and r.gig_id in removed_ids
        )
        if len(on_disk) - len(rows) > SHRINK_TOLERANCE + removed_on_disk:
            log.error(
                "REFUSING to write %s: %d rows on disk but only %d to write "
                "(tolerance %d + %d deliberately archived). "
                "Keeping the on-disk file untouched.",
                path, len(on_disk), len(rows), SHRINK_TOLERANCE, removed_on_disk,
            )
            return WriteResult(path, refused=True)
        rendered = render(rows)
        if path.exists() and _content_equal(path.read_text(), rendered):
            return WriteResult(path)  # no changes — skip the write, spare an iCloud sync
        atomic_write_text(path, rendered)
    return WriteResult(path)
