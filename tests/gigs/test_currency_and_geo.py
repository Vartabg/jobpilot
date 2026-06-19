"""Tier-4: currency capture/conversion + geo-eligibility filter."""

from jobpilot.gigs.core import preferences, scorer
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.scrapers.comp import detect_currency


def _gig(**kw):
    base = dict(id="g-1", source="hn", title="Engineer", company="Co",
                description="", url="https://post.test/1")
    base.update(kw)
    return Gig(**base)


# --- currency detection ----------------------------------------------------

def test_detect_currency_token_and_symbols():
    assert detect_currency("$105,000 - $125,000 CAD + equity") == "CAD"
    assert detect_currency("£80k - £100k") == "GBP"
    assert detect_currency("€70k") == "EUR"
    assert detect_currency("$120k - $150k") == "USD"
    assert detect_currency("") == "USD"


def test_normalize_pay_converts_to_usd():
    cad = _gig(salary_min=105000, salary_max=125000, currency="CAD")
    usd = _gig(salary_min=105000, salary_max=125000, currency="USD")
    assert scorer._normalize_pay(cad) < scorer._normalize_pay(usd)


def test_sub_floor_cad_role_drops_when_usd_equivalent_is_low(monkeypatch):
    # 125K CAD ≈ 91K USD ≈ $45/hr — below the default $65 floor → dropped
    # (a confident two-ended salary parse).
    monkeypatch.setattr(preferences, "location_config",
                        lambda prefs=None: {"home_metro_tags": [], "require_home_or_remote": False})
    g = _gig(id="hn-cad", title="Founding Engineer", currency="CAD",
             salary_min=105000, salary_max=125000,
             description="agentic workflows, python, claude")
    kept = scorer.filter_and_rank([g], min_score=0)
    assert kept == []  # the USD-equivalent falls below the pay floor


# --- geo eligibility -------------------------------------------------------

_HOME = ["austin", "austin tx"]


def test_geo_excludes_must_live_elsewhere():
    g = _gig(location="Remote", description="REMOTE (MUST LIVE IN CANADA). Backend role.")
    assert scorer._geo_eligible(g, home_tags=_HOME, allow_remote=True) is False


def test_geo_keeps_remote_us_role():
    g = _gig(location="Remote", description="Remote, US-based team, must live in the US.")
    assert scorer._geo_eligible(g, home_tags=_HOME, allow_remote=True) is True


def test_geo_keeps_home_metro_role():
    g = _gig(location="Austin, TX", description="onsite")
    assert scorer._geo_eligible(g, home_tags=_HOME, allow_remote=True) is True


def test_geo_keeps_plain_remote():
    g = _gig(location="Remote", description="distributed team, work from anywhere")
    assert scorer._geo_eligible(g, home_tags=_HOME, allow_remote=True) is True


def test_geo_excludes_specific_other_city_onsite():
    g = _gig(location="San Francisco, CA", description="onsite five days a week")
    assert scorer._geo_eligible(g, home_tags=_HOME, allow_remote=True) is False


def test_geo_keeps_unknown_location():
    g = _gig(location="See post", description="great team building things")
    assert scorer._geo_eligible(g, home_tags=_HOME, allow_remote=True) is True


def test_filter_applies_geo_when_enabled(monkeypatch):
    monkeypatch.setattr(preferences, "location_config",
                        lambda prefs=None: {"home_metro_tags": _HOME,
                                            "require_home_or_remote": True, "allow_remote": True})
    canada = _gig(id="hn-ca", title="Founding Engineer", location="Remote",
                  description="must live in canada; python agent claude rag")
    remote = _gig(id="hn-ok", title="AI Engineer", location="Remote",
                  description="remote, python agent claude rag")
    kept = scorer.filter_and_rank([canada, remote], min_score=0)
    ids = {g.id for g in kept}
    assert "hn-ok" in ids and "hn-ca" not in ids
