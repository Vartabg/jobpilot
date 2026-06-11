"""Stable scraper IDs must not depend on Python's salted hash()."""

from jobpilot.gigs.core.scrapers.himalayas import _id_from_url as himalayas_id
from jobpilot.gigs.core.scrapers.ids import stable_url_suffix
from jobpilot.gigs.core.scrapers.weworkremotely import _id_from_url as wwr_id


def test_stable_url_suffix_is_deterministic() -> None:
    url = "https://example.com/jobs/no-slug-match"
    assert stable_url_suffix(url) == stable_url_suffix(url)


def test_wwr_fallback_id_uses_stable_digest() -> None:
    url = "https://weworkremotely.com/remote-jobs/12345-weird"
    assert wwr_id(url) == wwr_id(url)
    assert "wwr-" in wwr_id(url)


def test_himalayas_fallback_id_uses_stable_digest() -> None:
    url = "https://himalayas.app/job/unknown-shape"
    assert himalayas_id(url) == himalayas_id(url)