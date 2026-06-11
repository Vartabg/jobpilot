"""seen.json semantics: archived permanence and first-seen stamps."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from jobpilot.gigs.core import store


def _point_store_at(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(store, "SEEN_PATH", tmp_path / "seen.json")
    monkeypatch.setattr(store, "FIRST_SEEN_PATH", tmp_path / "first_seen.json")


def test_mark_archived_overwrites_and_filters(tmp_path: Path, monkeypatch) -> None:
    _point_store_at(tmp_path, monkeypatch)
    store.mark_seen(["a"])
    store.mark_archived(["a", "b"])
    seen = json.loads((tmp_path / "seen.json").read_text())
    assert seen["a"].startswith(store.ARCHIVED_PREFIX)  # overwritten
    assert seen["b"].startswith(store.ARCHIVED_PREFIX)
    assert store.filter_new(["a", "b", "c"]) == ["c"]


def test_mark_archived_noop_on_empty_list(tmp_path: Path, monkeypatch) -> None:
    _point_store_at(tmp_path, monkeypatch)
    store.mark_archived([])
    assert not (tmp_path / "seen.json").exists()


def test_mark_seen_keeps_first_timestamp(tmp_path: Path, monkeypatch) -> None:
    _point_store_at(tmp_path, monkeypatch)
    store.mark_seen(["a"])
    first = json.loads((tmp_path / "seen.json").read_text())["a"]
    store.mark_seen(["a"])
    assert json.loads((tmp_path / "seen.json").read_text())["a"] == first


def test_sync_first_seen_stamps_keeps_and_drops(tmp_path: Path, monkeypatch) -> None:
    _point_store_at(tmp_path, monkeypatch)
    old = (datetime.now() - timedelta(days=10)).isoformat()
    (tmp_path / "first_seen.json").write_text(json.dumps({
        "still-new": old,
        "decided-since": old,
    }))

    out = store.sync_first_seen(["still-new", "fresh"])

    assert out["still-new"] == old           # existing stamp preserved
    assert "decided-since" not in out        # no longer `new` → dropped
    assert datetime.fromisoformat(out["fresh"])  # stamped now
    assert json.loads((tmp_path / "first_seen.json").read_text()) == out


def test_sync_first_seen_survives_corrupt_file(tmp_path: Path, monkeypatch) -> None:
    _point_store_at(tmp_path, monkeypatch)
    (tmp_path / "first_seen.json").write_text("not json")
    out = store.sync_first_seen(["a"])
    assert set(out) == {"a"}
