"""Stage 4 — pipeline hygiene.

Covers: repost-aware merge (cross-source key vs. existing rows), re-scoring
still-`new` rows, the one-time hygiene migration (duplicate collapse +
legacy ID regeneration), auto-archive of stale `new` rows, and the
archive-aware shrink guard in write().
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from jobpilot.gigs.core import pipeline, store
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.pipeline import (
    ARCHIVE_AFTER_DAYS,
    Row,
    append_to_archive,
    hygiene_migration,
    merge_new_gigs,
    migrate_pipeline_hygiene,
    parse,
    parse_text,
    rescore_new_rows,
    split_archivable,
    write,
)
from jobpilot.gigs.core.scorer import score_gig
from jobpilot.gigs.core.scrapers.ids import stable_url_suffix


def _gig(**overrides) -> Gig:
    base = dict(
        id="wwr-abc123",
        source="wwr",
        title="AI Engineer",
        url="https://weworkremotely.com/jobs/1",
        company="Fusemachines",
        description="LLM agent pipelines in Python.",
        fit_score=80,
    )
    base.update(overrides)
    return Gig(**base)


def _point_store_at(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(store, "SEEN_PATH", tmp_path / "seen.json")
    monkeypatch.setattr(store, "FIRST_SEEN_PATH", tmp_path / "first_seen.json")


# ---- merge: reposts with fresh IDs must not pile up -----------------------


def test_merge_skips_repost_with_fresh_id_same_company_role() -> None:
    existing = [Row(gig_id="wwr-111", status="new", company="Fusemachines",
                    role="AI Engineer")]
    repost = _gig(id="wwr-999")  # new ID, same company+title
    merged = merge_new_gigs(existing, [repost])
    assert len(merged) == 1
    assert merged[0].gig_id == "wwr-111"


def test_merge_skips_repost_matched_via_ats_host() -> None:
    # The pipeline row was saved with an enriched greenhouse link; the
    # repost arrives with a different company spelling but the same ATS
    # apply URL host + title.
    existing = [Row(gig_id="wwr-111", status="new", company="Fusemachines",
                    role="AI Engineer",
                    apply="https://boards.greenhouse.io/fuse/jobs/1")]
    repost = _gig(
        id="rok-7", source="remoteok", company="Fusemachines Inc.",
        apply_url="https://boards.greenhouse.io/fuse/jobs/1",
    )
    merged = merge_new_gigs(existing, [repost])
    assert len(merged) == 1


def test_merge_collapses_key_duplicates_within_one_batch() -> None:
    # Two fresh gigs in the same batch sharing a key: only one row lands.
    merged = merge_new_gigs(
        [], [_gig(id="wwr-1"), _gig(id="rok-2", source="remoteok")],
    )
    assert len(merged) == 1


def test_merge_still_adds_genuinely_new_roles() -> None:
    existing = [Row(gig_id="wwr-111", status="new", company="Fusemachines",
                    role="AI Engineer")]
    other = _gig(id="wwr-222", title="Data Engineer")
    merged = merge_new_gigs(existing, [other])
    assert len(merged) == 2
    assert merged[1].gig_id == "wwr-222"


# ---- re-score still-`new` rows ---------------------------------------------


def test_rescore_updates_new_rows_from_collected_listing() -> None:
    gig = _gig()
    rows = [Row(gig_id=gig.id, status="new", company=gig.company,
                role=gig.title, score=999)]
    changed = rescore_new_rows(rows, [gig])
    assert changed == 1
    assert rows[0].score == score_gig(gig).fit_score
    assert rows[0].score != 999


def test_rescore_leaves_decided_rows_alone() -> None:
    gig = _gig()
    rows = [Row(gig_id=gig.id, status="saved", company=gig.company,
                role=gig.title, score=999, notes="mine")]
    assert rescore_new_rows(rows, [gig]) == 0
    assert rows[0].score == 999
    assert rows[0].status == "saved"
    assert rows[0].notes == "mine"


def test_rescore_matches_by_key_when_listing_has_fresh_id() -> None:
    repost = _gig(id="wwr-fresh")
    rows = [Row(gig_id="wwr-stale", status="new", company=repost.company,
                role=repost.title, score=999)]
    assert rescore_new_rows(rows, [repost]) == 1
    assert rows[0].score == score_gig(repost).fit_score


def test_rescore_reconstructs_gig_when_listing_is_gone() -> None:
    row = Row(gig_id="wwr-gone", status="new", company="Acme",
              role="AI Engineer", score=999)
    expected = score_gig(pipeline._gig_from_row(row)).fit_score
    assert rescore_new_rows([row], []) == 1
    assert row.score == expected


# ---- hygiene migration: collapse + legacy ID regeneration ------------------


_LEGACY_ID = "wwr-1234567890123456789"


def test_collapse_keeps_row_with_user_data() -> None:
    fresh = Row(gig_id="wwr-aaa", status="new", company="Fusemachines",
                role="AI Engineer", score=90)
    decided = Row(gig_id="wwr-bbb", status="saved", company="Fusemachines",
                  role="AI Engineer", notes="emailed Sam", score=10)
    kept, removed, _ = hygiene_migration([fresh, decided])
    assert kept == [decided]
    assert removed == [fresh]


def test_collapse_prefers_modern_id_when_neither_has_user_data() -> None:
    legacy = Row(gig_id=_LEGACY_ID, status="new", company="Fusemachines",
                 role="AI Engineer")
    modern = Row(gig_id="wwr-deadbeef1234", status="new",
                 company="Fusemachines", role="AI Engineer")
    kept, removed, _ = hygiene_migration([legacy, modern])
    assert kept == [modern]
    assert removed == [legacy]


def test_collapse_leaves_distinct_roles_alone() -> None:
    a = Row(gig_id="a", status="new", company="Acme", role="AI Engineer")
    b = Row(gig_id="b", status="new", company="Acme", role="Data Engineer")
    kept, removed, _ = hygiene_migration([a, b])
    assert kept == [a, b]
    assert removed == []


def test_legacy_id_regenerated_from_url() -> None:
    url = "https://weworkremotely.com/remote-jobs/acme-ai-engineer"
    row = Row(gig_id=_LEGACY_ID, status="new", company="Acme",
              role="AI Engineer", apply=url)
    kept, _, remapped = hygiene_migration([row])
    new_id = f"wwr-{stable_url_suffix(url)}"
    assert remapped == {_LEGACY_ID: new_id}
    assert kept[0].gig_id == new_id


def test_legacy_id_kept_when_no_http_url() -> None:
    row = Row(gig_id=_LEGACY_ID, status="new", company="Acme",
              role="AI Engineer", apply="mailto:jobs@acme.io")
    kept, _, remapped = hygiene_migration([row])
    assert remapped == {}
    assert kept[0].gig_id == _LEGACY_ID


def test_modern_ids_never_remapped() -> None:
    row = Row(gig_id="wwr-deadbeef1234", status="new", company="Acme",
              role="AI Engineer", apply="https://acme.io/jobs/1")
    _, _, remapped = hygiene_migration([row])
    assert remapped == {}


def test_migrate_pipeline_hygiene_end_to_end(tmp_path: Path, monkeypatch) -> None:
    _point_store_at(tmp_path, monkeypatch)
    md = tmp_path / "pipeline.md"
    archive = tmp_path / "pipeline_archive.md"
    marker = tmp_path / ".pipeline_hygiene_done"

    url = "https://weworkremotely.com/remote-jobs/fuse-ai-engineer"
    rows = [
        # Three Fusemachines duplicates: one decided, two stale `new`.
        Row(gig_id="wwr-keep", status="saved", company="Fusemachines",
            role="AI Engineer", notes="follow up"),
        Row(gig_id="wwr-dup1", status="new", company="Fusemachines",
            role="AI Engineer"),
        Row(gig_id=_LEGACY_ID, status="new", company="Fusemachines",
            role="AI Engineer"),
        # Unrelated legacy row whose ID gets regenerated.
        Row(gig_id="wwr-9876543210987654321", status="new", company="Beta",
            role="Data Engineer", apply=url),
    ]
    write(rows, md)

    counts = migrate_pipeline_hygiene(md, archive, marker)
    assert counts == {"collapsed": 2, "ids_regenerated": 1}

    after = parse(md)
    assert len(after) == 2
    by_company = {r.company: r for r in after}
    assert by_company["Fusemachines"].gig_id == "wwr-keep"
    assert by_company["Fusemachines"].notes == "follow up"
    assert by_company["Beta"].gig_id == f"wwr-{stable_url_suffix(url)}"

    # The collapsed rows are preserved in the archive sidecar...
    archived = parse_text(archive.read_text())
    assert {r.gig_id for r in archived} == {"wwr-dup1", _LEGACY_ID}
    # ...and retired in seen.json so they can never resurface.
    seen = json.loads((tmp_path / "seen.json").read_text())
    assert seen["wwr-dup1"].startswith(store.ARCHIVED_PREFIX)
    assert store.filter_new(["wwr-dup1", _LEGACY_ID]) == []

    # Second run is a no-op (marker-gated).
    assert migrate_pipeline_hygiene(md, archive, marker) == {
        "collapsed": 0, "ids_regenerated": 0,
    }
    assert len(parse(md)) == 2


def test_migrate_pipeline_hygiene_marks_done_on_empty_pipeline(
    tmp_path: Path,
) -> None:
    md = tmp_path / "pipeline.md"
    marker = tmp_path / ".done"
    counts = migrate_pipeline_hygiene(md, tmp_path / "archive.md", marker)
    assert counts == {"collapsed": 0, "ids_regenerated": 0}
    assert marker.exists()


# ---- auto-archive of stale `new` rows --------------------------------------


def _iso_days_ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat()


def test_split_archivable_by_first_seen_stamp() -> None:
    old = Row(gig_id="a", status="new")
    young = Row(gig_id="b", status="new")
    first_seen = {
        "a": _iso_days_ago(ARCHIVE_AFTER_DAYS + 1),
        "b": _iso_days_ago(ARCHIVE_AFTER_DAYS - 1),
    }
    keep, stale = split_archivable([old, young], first_seen)
    assert stale == [old]
    assert keep == [young]


def test_split_archivable_never_touches_decided_rows() -> None:
    saved = Row(gig_id="a", status="saved")
    custom = Row(gig_id="b", status="ghosted")
    first_seen = {k: _iso_days_ago(99) for k in ("a", "b")}
    keep, stale = split_archivable([saved, custom], first_seen)
    assert stale == []
    assert keep == [saved, custom]


def test_split_archivable_uses_saved_date_when_no_stamp() -> None:
    now = datetime(2026, 6, 11)
    dated = Row(gig_id="a", status="new",
                saved=(now - timedelta(days=20)).strftime("%-m/%-d"))
    keep, stale = split_archivable([dated], {}, now=now)
    assert stale == [dated]
    assert keep == []


def test_split_archivable_keeps_rows_it_cannot_date() -> None:
    undatable = Row(status="new", company="Hand", role="Added")  # no gig_id
    unstamped = Row(gig_id="x", status="new")  # id'd but no stamp yet
    keep, stale = split_archivable([undatable, unstamped], {})
    assert stale == []
    assert len(keep) == 2


def test_append_to_archive_creates_documented_header_once(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "pipeline_archive.md"
    append_to_archive([Row(gig_id="a", status="new", company="Acme",
                           role="X", notes="why")], archive)
    text = archive.read_text()
    assert text.startswith("# GigPilot Pipeline Archive")
    assert "sidecar" in text  # the header documents the format choice

    append_to_archive([Row(gig_id="b", status="new", company="Beta",
                           role="Y")], archive)
    text = archive.read_text()
    assert text.count("# GigPilot Pipeline Archive") == 1
    rows = parse_text(text)  # rows stay copy-back-able
    assert [r.gig_id for r in rows] == ["a", "b"]
    assert rows[0].notes == "why"


# ---- archive-aware shrink guard --------------------------------------------


def _five_rows() -> list[Row]:
    return [
        Row(status="new", score=90 - i, company=f"Co{i}", role=f"R{i}",
            gig_id=f"g-{i}")
        for i in range(5)
    ]


def test_write_without_removed_ids_resurrects_disk_rows(tmp_path: Path) -> None:
    out = tmp_path / "pipeline.md"
    rows = _five_rows()
    write(rows, out)
    write(rows[:2], out)  # not flagged as removed → carried back over
    assert len(parse(out)) == 5


def test_write_with_removed_ids_archives_past_the_guard(tmp_path: Path) -> None:
    out = tmp_path / "pipeline.md"
    rows = _five_rows()
    write(rows, out)
    removed = {"g-2", "g-3", "g-4"}  # 3 > SHRINK_TOLERANCE, but deliberate
    write(rows[:2], out, removed_ids=removed)
    kept = parse(out)
    assert {r.gig_id for r in kept} == {"g-0", "g-1"}


def test_write_guard_still_trips_beyond_declared_removals(
    tmp_path: Path, monkeypatch,
) -> None:
    out = tmp_path / "pipeline.md"
    rows = _five_rows()
    write(rows, out)
    before = out.read_text()
    # Preservation disabled: simulate a buggy merge that loses rows beyond
    # the ones deliberately archived.
    monkeypatch.setattr(
        pipeline, "_preserve_user_edits", lambda merged, on_disk, **kw: merged,
    )
    write([], out, removed_ids={"g-0"})  # 5 lost, only 1 declared + 2 tolerance
    assert out.read_text() == before


def test_archived_ids_survive_seen_prune_and_stay_filtered(
    tmp_path: Path, monkeypatch,
) -> None:
    _point_store_at(tmp_path, monkeypatch)
    ancient = _iso_days_ago(store.MAX_RETAIN_DAYS + 10)
    (tmp_path / "seen.json").write_text(json.dumps({
        "forgettable": ancient,
        "kept-forever": f"{store.ARCHIVED_PREFIX}{ancient}",
    }))
    store.mark_seen(["fresh"])  # any write runs the prune
    seen = json.loads((tmp_path / "seen.json").read_text())
    assert "forgettable" not in seen
    assert "kept-forever" in seen
    assert store.filter_new(["kept-forever", "brand-new"]) == ["brand-new"]
