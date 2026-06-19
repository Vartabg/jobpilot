"""Tier-1 mobile-apply fixes: link resolution, placeholder guard, offer match."""

from jobpilot.gigs.core import preferences, proposals
from jobpilot.gigs.core.models import Gig


def _gig(**kw):
    base = dict(id="hn-1", source="hn", title="Role", company="Co",
               description="", url="https://example-post.test/1")
    base.update(kw)
    return Gig(**base)


# --- offer/role matching ---------------------------------------------------

def test_backend_3d_mention_does_not_get_threejs_offer():
    g = _gig(title="Founding Engineer",
             description="geologic modelling; building 3D geological models; python backend, agent, claude")
    assert proposals.pick_offer(g) != "Interactive 3D performance rescue"


def test_real_frontend_3d_still_gets_threejs_offer():
    g = _gig(title="Frontend Engineer",
             description="interactive configurator with three.js / react-three-fiber and webgl perf work")
    assert proposals.pick_offer(g) == "Interactive 3D performance rescue"


# --- placeholder guard -----------------------------------------------------

def test_contains_placeholder_detects_leaks():
    assert proposals.contains_placeholder("see https://your-portfolio.example.com/work")
    assert proposals.contains_placeholder("github.com/your-handle")
    assert proposals.contains_placeholder("a clean line with atxbro.com") is None


# --- links fall through to the resolved portfolio --------------------------

def test_links_fall_through_to_portfolio_when_placeholder():
    prefs = {
        "identity": {"portfolio": "https://www.atxbro.com/"},
        "links": dict(preferences.DEFAULTS["links"]),  # both at placeholder
    }
    out = preferences.links(prefs)
    assert out["work_page"] == "https://www.atxbro.com"
    assert out["service_page"] == "https://www.atxbro.com"
    assert "example.com" not in out["work_page"]


def test_explicit_links_are_kept():
    prefs = {
        "identity": {"portfolio": "https://www.atxbro.com/"},
        "links": {"work_page": "https://www.atxbro.com/work",
                  "service_page": "https://www.atxbro.com/services"},
    }
    out = preferences.links(prefs)
    assert out["work_page"] == "https://www.atxbro.com/work"
    assert out["service_page"] == "https://www.atxbro.com/services"


def test_placeholder_portfolio_does_not_propagate():
    # A fresh clone (portfolio still the default placeholder) must NOT copy the
    # placeholder portfolio into the link pages — they stay the link defaults.
    prefs = {
        "identity": {"portfolio": preferences.DEFAULTS["identity"]["portfolio"]},
        "links": dict(preferences.DEFAULTS["links"]),
    }
    out = preferences.links(prefs)
    assert out["work_page"] == preferences.DEFAULTS["links"]["work_page"]
