from jobpilot.gigs.cli import _include_upwork_exports, _scrapers


def test_upwork_exports_are_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("GIGPILOT_INCLUDE_UPWORK", raising=False)

    names = [name for name, _ in _scrapers()]

    assert not _include_upwork_exports()
    assert "Upwork exports" not in names


def test_upwork_exports_can_be_enabled(monkeypatch) -> None:
    monkeypatch.setenv("GIGPILOT_INCLUDE_UPWORK", "1")

    names = [name for name, _ in _scrapers()]

    assert _include_upwork_exports()
    assert "Upwork exports" in names
