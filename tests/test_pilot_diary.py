"""Pilot diary — live trail for watching / improving the local helper."""

from jobpilot.core import pilot_diary


def test_start_append_and_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot_diary, "DIARY_DIR", tmp_path / "pilot-diary")
    monkeypatch.setattr(
        pilot_diary, "CURRENT_LINK", tmp_path / "pilot-diary" / "current.md"
    )
    monkeypatch.setattr(
        pilot_diary, "ACTIVE_META", tmp_path / "pilot-diary" / ".active"
    )

    path = pilot_diary.start_session(label="test")
    assert path.is_file()
    assert pilot_diary.is_active()

    pilot_diary.append("jobs", "Listed 3 open jobs")
    pilot_diary.append("note", "Picked P-1 AI for a closer look")

    text = path.read_text(encoding="utf-8")
    assert "Listed 3 open jobs" in text
    assert "P-1 AI" in text

    tail = pilot_diary.read_tail(lines=20)
    assert "live" in tail.lower() or "Listed" in tail

    closed = pilot_diary.end_session(summary="test done")
    assert closed == path
    assert not pilot_diary.is_active()


def test_auto_is_silent_without_session(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot_diary, "DIARY_DIR", tmp_path / "pilot-diary")
    monkeypatch.setattr(
        pilot_diary, "CURRENT_LINK", tmp_path / "pilot-diary" / "current.md"
    )
    monkeypatch.setattr(
        pilot_diary, "ACTIVE_META", tmp_path / "pilot-diary" / ".active"
    )

    # Should not raise
    pilot_diary.auto("jobs", "nothing open")
    assert pilot_diary.active_path() is None
