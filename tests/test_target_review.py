"""Target review state is agent-neutral and role-scoped."""

from types import SimpleNamespace

from jobpilot.core.target_review import resolve_target_review_path, review_state_for_job


def _job(title: str = "Customer Engineer") -> SimpleNamespace:
    return SimpleNamespace(
        company="Acme",
        title=title,
        url="https://jobs.example.test/acme/1",
    )


def test_neutral_review_report_is_preferred_over_legacy_name(tmp_path) -> None:
    legacy = tmp_path / "claude-vetted-targets-2026-08-14.json"
    neutral = tmp_path / "agent-reviewed-targets-2026-08-14.json"
    legacy.write_text('{"targets": []}')
    neutral.write_text('{"targets": []}')

    assert resolve_target_review_path(directory=tmp_path) == neutral


def test_legacy_report_remains_readable_but_output_is_neutral(tmp_path) -> None:
    report = tmp_path / "claude-vetted-targets-2026-08-14.json"
    report.write_text(
        '{"targets":[{"company":"Acme","title":"Customer Engineer",'
        '"decision":"keep","materials_status":"ready"}]}'
    )

    assert review_state_for_job(_job(), review_path=report) == "ready"


def test_review_never_crosses_to_a_different_role(tmp_path) -> None:
    report = tmp_path / "agent-reviewed-targets-2026-08-14.json"
    report.write_text(
        '{"targets":[{"company":"Acme","title":"Customer Engineer",'
        '"decision":"keep","materials_status":"ready"}]}'
    )

    assert (
        review_state_for_job(_job("Senior Customer Engineer"), review_path=report)
        == "unvetted"
    )
