import pytest

from jobpilot.engine.domain import (
    Lane,
    Listing,
    canonicalize_role_url,
    identify_role,
    opportunity_id,
)


class TestCanonicalUrl:
    def test_greenhouse_hosts_collapse_and_tracking_drops(self):
        a = canonicalize_role_url(
            "https://job-boards.greenhouse.io/acme/jobs/123?gh_src=abc&utm_source=x"
        )
        b = canonicalize_role_url("https://boards.greenhouse.io/acme/jobs/123/")
        assert a == b == "https://boards.greenhouse.io/acme/jobs/123"

    def test_lever_and_ashby_drop_every_query_parameter(self):
        assert canonicalize_role_url(
            "https://jobs.lever.co/acme/abc-1?lever-source=li"
        ) == ("https://jobs.lever.co/acme/abc-1")
        assert canonicalize_role_url(
            "https://jobs.ashbyhq.com/acme/uuid-9/application?src=x"
        ) == ("https://jobs.ashbyhq.com/acme/uuid-9/application")

    def test_unknown_sites_keep_meaningful_parameters(self):
        assert canonicalize_role_url(
            "https://example.com/careers?id=7&utm_medium=mail"
        ) == ("https://example.com/careers?id=7")

    def test_garbage_is_not_a_url(self):
        assert canonicalize_role_url("") == ""
        assert canonicalize_role_url("not a url") == ""
        assert canonicalize_role_url("https://host:notaport/x") == ""


class TestIdentity:
    def test_provider_native_ids(self):
        greenhouse = identify_role("https://boards.greenhouse.io/acme/jobs/123")
        lever = identify_role("https://jobs.lever.co/acme/abc-1")
        ashby = identify_role("https://jobs.ashbyhq.com/acme/uuid-9")
        assert greenhouse.key == "greenhouse:acme:123"
        assert lever.key == "lever:acme:abc-1"
        assert ashby.key == "ashby:acme:uuid-9"

    def test_embedded_greenhouse_board(self):
        identity = identify_role("https://acme.com/careers?gh_jid=555&for=acme")
        assert identity.provider == "greenhouse"
        assert identity.key == "greenhouse:acme:555"

    def test_unknown_site_uses_a_url_hash(self):
        assert identify_role("https://example.com/careers?id=7").key.startswith("url:")

    def test_same_role_through_different_urls_is_one_opportunity(self):
        first = Listing(
            "Acme",
            "Engineer",
            url="https://job-boards.greenhouse.io/acme/jobs/123?gh_src=li",
        )
        second = Listing(
            "ACME Inc", "Engineer II", url="https://boards.greenhouse.io/acme/jobs/123"
        )
        assert opportunity_id(first.identity_key()) == opportunity_id(
            second.identity_key()
        )


class TestListingFallback:
    def test_listing_without_url_falls_back_to_company_and_title(self):
        listing = Listing("Acme, Inc.", "Field Service Engineer", lane=Lane.GIG)
        assert listing.identity_key() == "role:acme inc:field service engineer"

    def test_listing_with_nothing_to_identify_it_is_rejected(self):
        with pytest.raises(ValueError):
            Listing("", "Engineer").identity_key()
