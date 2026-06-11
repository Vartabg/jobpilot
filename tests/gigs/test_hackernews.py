from jobpilot.gigs.core.scrapers.hackernews import (
    _extract_apply_url,
    _first_meaningful_line,
    _looks_like_candidate_post,
    _parse_header_line,
    _strip_html,
)


def test_strip_html_preserves_paragraph_breaks() -> None:
    html = "<p>Acme AI | Senior Engineer | NYC</p><p>By turning legal code into AI code, Acme enables enterprises.</p>"
    out = _strip_html(html)
    first = _first_meaningful_line(out)
    assert first.startswith("Acme AI")
    assert "By turning" not in first


def test_parse_header_line_extracts_company_and_role() -> None:
    company, role = _parse_header_line("Acme AI | Senior Engineer | NYC | $180-200K")
    assert company == "Acme AI"
    assert role == "Senior Engineer"


def test_parse_header_line_handles_no_pipes() -> None:
    company, role = _parse_header_line("Acme AI is hiring engineers")
    assert company == ""
    assert role.startswith("Acme AI is hiring")


def test_candidate_style_hn_post_is_filtered() -> None:
    text = """
    Location: Fremont, CA
    Remote: Yes
    Willing to relocate: No
    Technologies: Python, distributed systems, LLMs
    Résumé/CV: https://example.com
    """

    assert _looks_like_candidate_post(text)


def test_company_hiring_post_is_kept() -> None:
    text = """
    Acme AI | Staff Engineer | Remote
    We are hiring a Python engineer to build LLM workflow automation.
    Apply at https://example.com/jobs
    """

    assert not _looks_like_candidate_post(text)


def test_apply_url_prefers_jobs_inbox_mailto() -> None:
    html = (
        'Acme AI | Staff Engineer | Remote. Email '
        '<a href="mailto:jobs@acme.ai">jobs@acme.ai</a> with your resume. '
        'Or visit <a href="https://acme.ai">our site</a>.'
    )
    assert _extract_apply_url(html, _strip_html(html)) == "mailto:jobs@acme.ai"


def test_apply_url_picks_greenhouse_board_over_homepage() -> None:
    html = (
        'Visit <a href="https://acme.ai">our site</a> or '
        'apply at <a href="https://boards.greenhouse.io/acme/jobs/123">Greenhouse</a>.'
    )
    assert (
        _extract_apply_url(html, _strip_html(html))
        == "https://boards.greenhouse.io/acme/jobs/123"
    )


def test_apply_url_falls_back_to_plaintext_jobs_inbox() -> None:
    html = "We are hiring. Email jobs@acme.ai with your resume."
    assert _extract_apply_url(html, _strip_html(html)) == "mailto:jobs@acme.ai"


def test_apply_url_falls_back_to_plaintext_ats_url() -> None:
    text = "Apply at https://jobs.lever.co/acme/abc-123 by Friday."
    assert (
        _extract_apply_url(text, text)
        == "https://jobs.lever.co/acme/abc-123"
    )


def test_apply_url_blank_when_no_signal() -> None:
    html = "We are hiring. DM me on Twitter for details."
    assert _extract_apply_url(html, _strip_html(html)) == ""
