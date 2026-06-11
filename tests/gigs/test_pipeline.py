"""Pipeline schema, parser, renderer, merge logic, and migration tests."""
from __future__ import annotations

import logging
from pathlib import Path

from jobpilot.gigs.core import pipeline
from jobpilot.gigs.core.models import Gig
from datetime import datetime

from jobpilot.gigs.core.pipeline import (
    Row,
    excluded_ids,
    merge_new_gigs,
    parse,
    parse_last_touched,
    parse_text,
    render,
    stamp_status_changes,
    write,
)


def _gig(**overrides) -> Gig:
    base = dict(
        id="hn-1",
        source="hn",
        title="Senior Engineer",
        url="https://news.ycombinator.com/item?id=1",
        apply_url="mailto:jobs@acme.io",
        company="Acme AI",
        description="Python, RAG, agent orchestration.",
        location="NYC",
        salary_min=180000,
        salary_max=200000,
        fit_score=95,
    )
    base.update(overrides)
    return Gig(**base)


# ---- render → parse roundtrip ------------------------------------------


def test_render_then_parse_preserves_row_data() -> None:
    rows = [
        Row(status="new", score=100, company="Acme", role="Sr Eng",
            pay="$180-200K", apply="mailto:jobs@acme.io", gig_id="hn-1"),
        Row(status="sent", score=95, company="Bjak", role="QA Eng",
            pay="", apply="https://bjak.my/en", saved="5/3", last_touched="5/4",
            next_action="follow up by 5/10", notes="applied via email", gig_id="wwr-2"),
    ]
    rendered = render(rows)
    parsed = parse_text(rendered)
    assert len(parsed) == 2
    by_id = {r.gig_id: r for r in parsed}
    assert by_id["hn-1"].status == "new"
    assert by_id["hn-1"].score == 100
    assert by_id["hn-1"].company == "Acme"
    assert by_id["hn-1"].role == "Sr Eng"
    assert by_id["hn-1"].apply == "mailto:jobs@acme.io"
    assert by_id["wwr-2"].status == "sent"
    assert by_id["wwr-2"].next_action == "follow up by 5/10"
    assert by_id["wwr-2"].notes == "applied via email"
    assert by_id["wwr-2"].saved == "5/3"


# ---- status normalization ----------------------------------------------


def test_status_shorthand_normalized_on_parse() -> None:
    text = """
| Status | Score | Company — Role | Pay | Apply | Saved | Last touched | Next action | Notes |
|---|---|---|---|---|---|---|---|---|
| s | 100 | Acme — X | | | | | | | <!-- gig_id:a -->
| save | 95 | Beta — Y | | | | | | | <!-- gig_id:b -->
| p | 90 | Gamma — Z | | | | | | | <!-- gig_id:c -->
| pass | 85 | Delta — W | | | | | | | <!-- gig_id:d -->
|  | 80 | Eps — V | | | | | | | <!-- gig_id:e -->
"""
    rows = parse_text(text)
    statuses = {r.gig_id: r.status for r in rows}
    assert statuses == {
        "a": "saved",
        "b": "saved",
        "c": "passed",
        "d": "passed",
        "e": "new",
    }


# ---- merge logic --------------------------------------------------------


def test_merge_new_gigs_appends_only_unseen_ids() -> None:
    existing = [Row(gig_id="hn-1", status="saved", company="Acme", role="X")]
    new = [_gig(id="hn-1"), _gig(id="hn-2", company="Bjak")]
    merged = merge_new_gigs(existing, new)
    assert len(merged) == 2  # hn-1 is preserved, hn-2 is added
    assert merged[0].gig_id == "hn-1" and merged[0].status == "saved"
    assert merged[1].gig_id == "hn-2" and merged[1].status == "new"


def test_merge_preserves_user_edits_on_existing_rows() -> None:
    existing = [
        Row(gig_id="hn-1", status="sent", company="Acme", role="X",
            saved="5/3", last_touched="5/4", next_action="ping by 5/10"),
    ]
    merged = merge_new_gigs(existing, [_gig(id="hn-1")])
    # Existing row unchanged, no duplicate added
    assert len(merged) == 1
    assert merged[0].next_action == "ping by 5/10"


# ---- stamp status changes ----------------------------------------------


def test_stamp_records_today_when_status_flips() -> None:
    before = [Row(gig_id="x", status="new")]
    after = [Row(gig_id="x", status="saved")]
    stamp_status_changes(before, after, today="5/8")
    assert after[0].last_touched == "5/8"
    assert after[0].saved == "5/8"


def test_stamp_does_nothing_when_status_unchanged() -> None:
    before = [Row(gig_id="x", status="sent", last_touched="5/3")]
    after = [Row(gig_id="x", status="sent", last_touched="5/3")]
    stamp_status_changes(before, after, today="5/8")
    assert after[0].last_touched == "5/3"  # untouched


# ---- excluded_ids -------------------------------------------------------


def test_excluded_ids_includes_decided_statuses_only() -> None:
    rows = [
        Row(gig_id="a", status="new"),
        Row(gig_id="b", status="saved"),
        Row(gig_id="c", status="sent"),
        Row(gig_id="d", status="passed"),
        Row(gig_id="e", status="replied"),
        Row(gig_id="f", status="custom-not-in-enum"),
    ]
    assert excluded_ids(rows) == {"b", "c", "d", "e"}


# ---- write/parse via filesystem -----------------------------------------


def test_write_then_parse_roundtrips_via_disk(tmp_path: Path) -> None:
    rows = [Row(status="new", score=100, company="Acme", role="X", gig_id="hn-1")]
    out = tmp_path / "pipeline.md"
    write(rows, out)
    assert out.exists()
    parsed = parse(out)
    assert len(parsed) == 1
    assert parsed[0].gig_id == "hn-1"


def test_parse_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert parse(tmp_path / "nope.md") == []


# ---- pipe character handling -------------------------------------------


def test_pipe_in_cell_content_is_escaped_to_slash() -> None:
    row = Row(status="new", company="Pipe|Co", role="A|B", gig_id="x")
    rendered = render([row])
    parsed = parse_text(rendered)
    assert parsed[0].company == "Pipe/Co"
    assert parsed[0].role == "A/B"


# ---- Row helper properties ---------------------------------------------


def test_parse_last_touched_handles_year_boundary() -> None:
    today = datetime(2026, 1, 5)
    lt = parse_last_touched("12/28", today)
    assert lt is not None
    assert lt.year == 2025
    assert (today - lt).days > 7


def test_row_helper_status_buckets() -> None:
    assert Row(status="new").excluded_from_future is False
    assert Row(status="saved").excluded_from_future is True
    assert Row(status="saved").is_actively_pursuing is True
    assert Row(status="sent").is_actively_pursuing is True
    assert Row(status="replied").is_actively_pursuing is False
    assert Row(status="replied").is_replied is True
    assert Row(status="interview").is_replied is True
    assert Row(status="passed").is_replied is False


# ---- status snapshot (between-run diff base) ------------------------------


def test_status_snapshot_roundtrip_and_corrupt_file(tmp_path: Path) -> None:
    snap = tmp_path / "pipeline_prev_status.json"
    assert pipeline.load_status_snapshot(snap) == []  # first run: no snapshot
    pipeline.save_status_snapshot(
        [Row(gig_id="a", status="saved"), Row(status="new")],  # no-id row skipped
        snap,
    )
    loaded = pipeline.load_status_snapshot(snap)
    assert [(r.gig_id, r.status) for r in loaded] == [("a", "saved")]
    snap.write_text("not json")
    assert pipeline.load_status_snapshot(snap) == []


def _simulated_digest_run(md: Path, snap: Path, ranked: list[Gig], today: str) -> None:
    """Mirror cli.digest's pipeline flow: parse → diff vs snapshot → merge →
    write → persist snapshot at end of run."""
    existing = stamp_status_changes(
        pipeline.load_status_snapshot(snap), parse(md), today=today,
    )
    updated = merge_new_gigs(existing, ranked)
    write(updated, md)
    pipeline.save_status_snapshot(updated, snap)


def test_snapshot_diff_stamps_user_edit_between_runs(tmp_path: Path) -> None:
    md = tmp_path / "pipeline.md"
    snap = tmp_path / "pipeline_prev_status.json"

    # Run 1: a fresh gig lands as `new`.
    _simulated_digest_run(md, snap, [_gig(id="hn-1")], today="6/10")
    assert parse(md)[0].status == "new"
    assert parse(md)[0].last_touched == ""

    # Between runs: the user flips Status to `s` (saved) from their phone.
    md.write_text(md.read_text().replace("| new | 95 |", "| s | 95 |"))

    # Run 2: the diff against the snapshot stamps Saved + Last touched.
    _simulated_digest_run(md, snap, [], today="6/11")
    row = parse(md)[0]
    assert row.status == "saved"
    assert row.saved == "6/11"
    assert row.last_touched == "6/11"

    # Run 3: no further edit — stamps are not re-applied with a new date.
    _simulated_digest_run(md, snap, [], today="6/12")
    row = parse(md)[0]
    assert row.saved == "6/11"
    assert row.last_touched == "6/11"


# ---- round-trip safety rails ---------------------------------------------


def test_write_refuses_to_shrink_and_keeps_disk_file(
    tmp_path: Path, monkeypatch, caplog,
) -> None:
    out = tmp_path / "pipeline.md"
    rows = [
        Row(status="new", score=90 - i, company=f"Co{i}", role="X", gig_id=f"g-{i}")
        for i in range(5)
    ]
    write(rows, out)
    before = out.read_text()

    # Simulate a regression that silently loses rows (preservation disabled).
    monkeypatch.setattr(
        pipeline, "_preserve_user_edits", lambda merged, on_disk, **kw: merged,
    )
    with caplog.at_level(logging.ERROR, logger="jobpilot.gigs.core.pipeline"):
        refused = write(rows[:1], out)  # shrink of 4 > tolerance — must abort
    assert out.read_text() == before
    assert "REFUSING" in caplog.text
    # The caller must be able to see the refusal — a refused run must not
    # mark gigs seen or snapshot statuses (they were never persisted).
    assert refused.refused is True

    ok = write(rows[2:], out)  # shrink of exactly SHRINK_TOLERANCE — allowed
    assert ok.refused is False
    assert len(parse(out)) == 3


def test_write_preserves_rows_hand_added_on_disk(tmp_path: Path) -> None:
    out = tmp_path / "pipeline.md"
    known = [Row(status="new", score=90, company="Acme", role="X", gig_id="hn-1")]
    write(known, out)

    # User hand-adds rows from their phone — with and without a gig_id marker.
    text = out.read_text().rstrip("\n")
    text += "\n| sent | 88 | Beta — Y | | | | | | | <!-- gig_id:hand-1 -->"
    text += "\n| saved |  | Gamma — Z | | | | | | |\n"
    out.write_text(text)

    # The next write doesn't know about either row — both must survive.
    write(known, out)
    rows = {(r.company, r.role): r for r in parse(out)}
    assert len(rows) == 3
    assert rows[("Beta", "Y")].status == "sent"
    assert rows[("Gamma", "Z")].status == "saved"

    # And the marker-less row is not duplicated by subsequent writes.
    write(parse(out), out)
    assert len(parse(out)) == 3


def test_write_skips_rewrite_when_nothing_changed(tmp_path: Path) -> None:
    out = tmp_path / "pipeline.md"
    rows = [Row(status="new", score=90, company="Acme", role="X", gig_id="hn-1")]
    write(rows, out)
    before = out.stat()
    before_text = out.read_text()

    write(rows, out)
    after = out.stat()
    assert after.st_ino == before.st_ino  # atomic replace never happened
    assert out.read_text() == before_text


def test_write_still_writes_when_rows_changed(tmp_path: Path) -> None:
    out = tmp_path / "pipeline.md"
    write([Row(status="new", score=90, company="Acme", role="X", gig_id="hn-1")], out)
    before = out.stat()
    # Score is a digest-owned column, so a rescore must actually hit disk.
    write([Row(status="new", score=95, company="Acme", role="X", gig_id="hn-1")], out)
    assert out.stat().st_ino != before.st_ino
    assert parse(out)[0].score == 95


def test_preserve_keeps_fresh_stamps_when_disk_dates_blank() -> None:
    # A stamp applied this run must survive _preserve_user_edits even though
    # disk (written before the stamp) has blank Saved / Last touched.
    merged = [Row(gig_id="x", status="saved", saved="6/11", last_touched="6/11")]
    on_disk = [Row(gig_id="x", status="saved")]
    out = pipeline._preserve_user_edits(merged, on_disk)
    assert out[0].saved == "6/11"
    assert out[0].last_touched == "6/11"

    # But a date the user typed on disk still wins over ours.
    on_disk = [Row(gig_id="x", status="saved", saved="6/1", last_touched="6/2")]
    out = pipeline._preserve_user_edits(merged, on_disk)
    assert out[0].saved == "6/1"
    assert out[0].last_touched == "6/2"


def test_preserve_keeps_notes_the_digest_appended_to() -> None:
    # A digest-side annotation appended to Notes after the run's first write
    # must not be stripped by the older on-disk copy on the second write.
    merged = [Row(gig_id="x", status="saved", notes="auto: drafted")]
    on_disk = [Row(gig_id="x", status="saved")]
    out = pipeline._preserve_user_edits(merged, on_disk)
    assert out[0].notes == "auto: drafted"

    # Same when the user already had notes the annotation was appended to.
    merged = [Row(gig_id="x", status="saved", notes="ping Sam auto: drafted")]
    on_disk = [Row(gig_id="x", status="saved", notes="ping Sam")]
    out = pipeline._preserve_user_edits(merged, on_disk)
    assert out[0].notes == "ping Sam auto: drafted"


def test_preserve_disk_notes_win_when_user_edited_mid_run() -> None:
    # A mid-run rewrite from the phone is not an append — disk wins.
    merged = [Row(gig_id="x", status="saved", notes="ping Sam auto: drafted")]
    on_disk = [Row(gig_id="x", status="saved", notes="they emailed back")]
    out = pipeline._preserve_user_edits(merged, on_disk)
    assert out[0].notes == "they emailed back"

    # A mid-run append from the phone (merged is the older, shorter value)
    # is also a user edit — disk wins.
    merged = [Row(gig_id="x", status="saved", notes="ping Sam")]
    on_disk = [Row(gig_id="x", status="saved", notes="ping Sam on Friday")]
    out = pipeline._preserve_user_edits(merged, on_disk)
    assert out[0].notes == "ping Sam on Friday"


def test_write_persists_notes_appended_after_first_write(tmp_path: Path) -> None:
    # Regression: the digest writes the pipeline, then appends an annotation
    # in memory and writes again. The second write must not let the older
    # on-disk copy strip it (and must not be swallowed by the no-change skip).
    out = tmp_path / "pipeline.md"
    rows = [Row(status="saved", score=90, company="Acme", role="X", gig_id="hn-1")]
    write(rows, out)
    rows[0].notes = "auto: drafted"
    write(rows, out)
    assert parse(out)[0].notes == "auto: drafted"


# ---- legacy reminder_created literal (pre-sidecar bookkeeping) -----------


def test_parse_strips_legacy_reminder_flag_from_notes() -> None:
    text = """
| Status | Score | Company — Role | Pay | Apply | Saved | Last touched | Next action | Notes |
|---|---|---|---|---|---|---|---|---|
| saved | 90 | Acme — X | | | | | | pass:low-pay reminder_created | <!-- gig_id:a -->
| sent | 80 | Beta — Y | | | | | | reminder_created | <!-- gig_id:b -->
| new | 70 | Gamma — Z | | | | | | keep me | <!-- gig_id:c -->
"""
    rows = parse_text(text)
    by_id = {r.gig_id: r for r in rows}
    assert by_id["a"].notes == "pass:low-pay"
    assert by_id["a"].legacy_reminder_flag is True
    assert by_id["b"].notes == ""
    assert by_id["b"].legacy_reminder_flag is True
    assert by_id["c"].notes == "keep me"
    assert by_id["c"].legacy_reminder_flag is False
    # The next render no longer carries the literal — Notes is clean.
    assert "reminder_created" not in render(rows)
