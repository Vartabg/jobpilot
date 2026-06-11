"""WeWorkRemotely apply-URL extraction — bypasses WWR's gated apply flow
by pulling the company's real mailto/ATS link from the listing page HTML."""
from __future__ import annotations

from unittest.mock import patch

from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.scrapers.weworkremotely import (
    _extract_from_description,
    enrich_apply_urls,
    extract_apply_url_from_listing,
)


# ---- description-based extraction (no network) --------------------------


def test_desc_picks_plaintext_jobs_inbox_first() -> None:
    desc = "URL: https://acme.com  Send resume to jobs@acme.com to apply."
    assert _extract_from_description(desc) == "mailto:jobs@acme.com"


def test_desc_picks_careers_url_over_homepage() -> None:
    desc = "URL: https://acme.com — apply at https://acme.com/careers/founding"
    assert _extract_from_description(desc) == "https://acme.com/careers/founding"


def test_desc_falls_back_to_structured_url() -> None:
    desc = "Headquarters: NYC URL: https://acme.com More about us."
    assert _extract_from_description(desc) == "https://acme.com"


def test_desc_returns_empty_when_no_url_or_email() -> None:
    assert _extract_from_description("A pure prose description.") == ""


def test_enrich_uses_description_first_no_network(monkeypatch) -> None:
    g = Gig(
        id="wwr-1",
        source="wwr",
        title="X",
        url="https://weworkremotely.com/x",
        description="URL: https://acme.com More text.",
    )
    with patch("jobpilot.gigs.core.scrapers.weworkremotely.requests.get") as get:
        enrich_apply_urls([g])
    assert g.apply_url == "https://acme.com"
    assert get.call_count == 0  # never hit the network


def test_enrich_falls_back_to_google_search_when_no_other_signal() -> None:
    g = Gig(
        id="wwr-1",
        source="wwr",
        title="X",
        url="https://weworkremotely.com/x",
        company="Mystery Co",
        description="No URL or email in this description.",
    )
    with patch("jobpilot.gigs.core.scrapers.weworkremotely.requests.get") as get:
        get.return_value.text = "<html>no apply link</html>"
        get.return_value.raise_for_status = lambda: None
        enrich_apply_urls([g])
    assert g.apply_url.startswith("https://www.google.com/search?q=")
    assert "Mystery%20Co" in g.apply_url or "Mystery+Co" in g.apply_url


def test_extract_prefers_mailto_jobs_inbox() -> None:
    html = """
    <html><body>
      <a href="https://weworkremotely.com/help">Help</a>
      <a href="mailto:jobs@acme.io">Apply for this position</a>
      <a href="https://acme.io">Acme homepage</a>
    </body></html>
    """
    assert extract_apply_url_from_listing(html) == "mailto:jobs@acme.io"


def test_extract_picks_ats_link_over_homepage() -> None:
    html = """
    <a href="https://acme.io">Visit our site</a>
    <a href="https://boards.greenhouse.io/acme/jobs/123">Apply on Greenhouse</a>
    """
    assert (
        extract_apply_url_from_listing(html)
        == "https://boards.greenhouse.io/acme/jobs/123"
    )


def test_extract_skips_wwr_internal_and_social_share_links() -> None:
    html = """
    <a href="https://weworkremotely.com/account/sign_up">Sign Up</a>
    <a href="/help">Help</a>
    <a href="https://twitter.com/intent/tweet?text=apply">Share</a>
    <a href="mailto:jobs@acme.io">Apply</a>
    """
    assert extract_apply_url_from_listing(html) == "mailto:jobs@acme.io"


def test_extract_falls_back_to_plaintext_jobs_email() -> None:
    html = (
        "<p>To apply, send your resume to <strong>jobs@acme.io</strong> "
        "with the subject 'Senior Engineer'.</p>"
    )
    assert extract_apply_url_from_listing(html) == "mailto:jobs@acme.io"


def test_extract_returns_empty_when_no_signal() -> None:
    html = (
        "<p>This role is interesting. Visit "
        "<a href='https://weworkremotely.com'>WWR</a> for similar.</p>"
    )
    assert extract_apply_url_from_listing(html) == ""


def test_enrich_apply_urls_skips_non_wwr() -> None:
    hn_gig = Gig(id="hn-1", source="hn", title="X", url="https://news.ycombinator.com/item?id=1")
    wwr_gig = Gig(id="wwr-1", source="wwr", title="Y", url="https://weworkremotely.com/x")

    with patch("jobpilot.gigs.core.scrapers.weworkremotely.requests.get") as get:
        get.return_value.text = '<a href="mailto:jobs@acme.io">Apply</a>'
        get.return_value.raise_for_status = lambda: None
        out = enrich_apply_urls([hn_gig, wwr_gig])

    assert hn_gig.apply_url == ""
    assert wwr_gig.apply_url == "mailto:jobs@acme.io"
    assert get.call_count == 1


def test_enrich_apply_urls_respects_existing_apply_url() -> None:
    g = Gig(
        id="wwr-1",
        source="wwr",
        title="X",
        url="https://weworkremotely.com/x",
        apply_url="mailto:already@set.com",
    )
    with patch("jobpilot.gigs.core.scrapers.weworkremotely.requests.get") as get:
        enrich_apply_urls([g])
    assert get.call_count == 0  # didn't hit the network
    assert g.apply_url == "mailto:already@set.com"


def test_enrich_apply_urls_caps_at_max_fetch() -> None:
    gigs = [
        Gig(id=f"wwr-{i}", source="wwr", title="X", url=f"https://weworkremotely.com/{i}")
        for i in range(30)
    ]
    with patch("jobpilot.gigs.core.scrapers.weworkremotely.requests.get") as get:
        get.return_value.text = ""
        get.return_value.raise_for_status = lambda: None
        enrich_apply_urls(gigs, max_fetch=5)
    assert get.call_count == 5


def test_enrich_apply_urls_silent_on_network_error() -> None:
    g = Gig(id="wwr-1", source="wwr", title="X", url="https://weworkremotely.com/x")
    with patch("jobpilot.gigs.core.scrapers.weworkremotely.requests.get", side_effect=RuntimeError("boom")):
        enrich_apply_urls([g])
    assert g.apply_url == ""
