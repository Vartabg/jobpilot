from jobpilot.core.opportunity_models import SourceRunResult
from jobpilot.core.source_health import summarize_source_runs


def test_source_health_distinguishes_zero_from_failure() -> None:
    summary = summarize_source_runs([
        SourceRunResult("greenhouse", "one", "success", result_count=4),
        SourceRunResult("ashby", "two", "zero"),
        SourceRunResult(
            "indeed",
            "private search query",
            "failure",
            error_type="HTTPError",
            error="scan failed",
        ),
        SourceRunResult("adzuna", "field role", "skipped"),
    ])

    assert summary.total == 4
    assert summary.healthy == 2
    assert summary.with_results == 1
    assert summary.zero_results == 1
    assert summary.failed == 1
    assert summary.skipped == 1
    assert summary.degraded is True
    assert "2/4 targets responded" in summary.summary_line
    assert summary.notices == (
        "indeed: 1 target — provider rejected the request or the target is unavailable",
    )
    assert "private search query" not in summary.summary_line
    assert "private search query" not in " ".join(summary.notices)


def test_source_health_groups_failures_without_raw_errors() -> None:
    summary = summarize_source_runs([
        SourceRunResult(
            "ashby", "one", "failure", error_type="HTTPError", error="secret one"
        ),
        SourceRunResult(
            "ashby", "two", "failure", error_type="HTTPError", error="secret two"
        ),
        SourceRunResult(
            "adzuna",
            "three",
            "failure",
            error_type="ConfigurationError",
        ),
    ])

    assert summary.notices == (
        "adzuna: 1 target — provider configuration is missing",
        "ashby: 2 targets — provider rejected the request or the target is unavailable",
    )
    assert "secret" not in " ".join(summary.notices)


def test_source_health_gives_specific_http_guidance() -> None:
    summary = summarize_source_runs([
        SourceRunResult("ashby", "dead", "failure", error_type="HTTP404"),
        SourceRunResult("indeed", "blocked", "failure", error_type="HTTP403"),
        SourceRunResult("google_jobs", "busy", "failure", error_type="HTTP429"),
    ])

    notices = " ".join(summary.notices)
    assert "target no longer exists" in notices
    assert "prefer a direct ATS source" in notices
    assert "retry later" in notices


def test_empty_source_health_is_explicit() -> None:
    summary = summarize_source_runs([])

    assert summary.summary_line == "Source health: no targets were scanned."
    assert summary.notices == ()
    assert summary.degraded is False
