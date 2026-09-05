"""Corruption must stay visible until the user repairs the original queue."""

import json
from pathlib import Path

import pytest

from jobpilot.core import queue_builder as qb


@pytest.fixture
def queue_path(tmp_path, monkeypatch):
    path = tmp_path / "queue.json"
    monkeypatch.setattr(qb, "QUEUE_PATH", path)
    monkeypatch.setattr(qb, "DATA_DIR", tmp_path)
    return path


def test_repeated_corrupt_reads_never_become_an_empty_queue(queue_path):
    original = '{"broken":'
    queue_path.write_text(original)
    for _ in range(2):
        with pytest.raises(ValueError):
            qb.load_queue()
        assert queue_path.read_text() == original
    backups = list(queue_path.parent.glob("queue.json.*.corrupt"))
    assert len(backups) == 1
    assert backups[0].read_text() == original


def test_distinct_corruption_keeps_both_recovery_copies(queue_path):
    for broken in ["broken-one", "broken-two"]:
        queue_path.write_text(broken)
        with pytest.raises(ValueError):
            qb.load_queue()
    backups = list(queue_path.parent.glob("queue.json.*.corrupt"))
    assert {p.read_text() for p in backups} == {"broken-one", "broken-two"}
    queue_path.write_text("[]")
    assert qb.load_queue() == []


def test_schema_error_keeps_valid_rows_available_for_recovery(queue_path):
    queue_path.write_text(json.dumps([{"id": "saved", "status": "skipped"}]))
    with pytest.raises(ValueError):
        qb.load_queue()
    assert "skipped" in queue_path.read_text()


def test_read_error_does_not_move_the_queue(queue_path, monkeypatch):
    queue_path.write_text("[]")
    original_read = Path.read_text

    def refuse(path, *args, **kwargs):
        if path == queue_path:
            raise PermissionError("test read refused")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse)
    with pytest.raises(OSError):
        qb.load_queue()
    assert queue_path.exists()
    assert not list(queue_path.parent.glob("*.corrupt"))


def test_missing_queue_is_still_a_normal_first_run(queue_path):
    assert qb.load_queue() == []
