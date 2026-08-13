"""Provider-aware role URL and identity normalization."""

from jobpilot.core.role_identity import (
    canonicalize_role_url,
    identify_role,
    is_safe_public_role_url,
)


def test_indeed_identity_keeps_jk_and_drops_tracking() -> None:
    url = "https://www.indeed.com/viewjob?jk=ABC123&utm_source=google&from=web"

    identity = identify_role(url)

    assert identity.provider == "indeed"
    assert identity.provider_job_id == "ABC123"
    assert identity.canonical_url == "https://www.indeed.com/viewjob?jk=ABC123"
    assert identity.key == "indeed:ABC123"


def test_direct_ats_identity_uses_tenant_and_job_id() -> None:
    identity = identify_role(
        "https://jobs.lever.co/Acme/role-123?lever-source=LinkedIn&utm_campaign=x"
    )

    assert identity.provider == "lever"
    assert identity.tenant == "acme"
    assert identity.provider_job_id == "role-123"
    assert identity.canonical_url == "https://jobs.lever.co/Acme/role-123"
    assert identity.key == "lever:acme:role-123"


def test_greenhouse_custom_page_preserves_identity_query() -> None:
    url = "https://acme.example/careers/job/?gh_jid=987&utm_medium=social"

    assert canonicalize_role_url(url) == "https://acme.example/careers/job?gh_jid=987"
    assert identify_role(url).provider_job_id == "987"


def test_greenhouse_embed_preserves_tenant_and_token_identity() -> None:
    first = identify_role(
        "https://boards.greenhouse.io/embed/job_app?for=acme&token=123"
    )
    second = identify_role(
        "https://boards.greenhouse.io/embed/job_app?for=other&token=123"
    )

    assert first.tenant == "acme"
    assert first.provider_job_id == "123"
    assert "for=acme" in first.canonical_url
    assert "token=123" in first.canonical_url
    assert first.key != second.key


def test_greenhouse_board_host_aliases_share_canonical_url_and_identity() -> None:
    legacy = identify_role(
        "https://boards.greenhouse.io/acme/jobs/123?gh_src=campaign"
    )
    current = identify_role(
        "https://job-boards.greenhouse.io/acme/jobs/123"
    )

    assert legacy.canonical_url == current.canonical_url
    assert legacy.key == current.key == "greenhouse:acme:123"


def test_unknown_provider_falls_back_to_canonical_url_identity() -> None:
    first = identify_role("https://EXAMPLE.com/jobs/42/?utm_source=x#apply")
    second = identify_role("https://example.com/jobs/42")

    assert first.key == second.key
    assert first.canonical_url == "https://example.com/jobs/42"


def test_public_posting_url_rejects_active_content_and_relative_paths() -> None:
    for unsafe in (
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "/jobs/42",
        "http://jobs.example.test/42",
    ):
        assert is_safe_public_role_url(unsafe) is False
    assert is_safe_public_role_url("https://jobs.example.test/42") is True
