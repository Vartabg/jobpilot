from __future__ import annotations

from copy import deepcopy

from jobpilot.core import profile_store
from jobpilot.core.profile_store import UserProfile
from jobpilot.gigs.core import packets, preferences
from jobpilot.gigs.core.models import Gig


class _FakeProfileStore:
    def __init__(self, profile: UserProfile) -> None:
        self.profile = profile

    def load(self) -> UserProfile:
        return self.profile


def _gig() -> Gig:
    return Gig(
        id="packet-test",
        source="hn",
        title="Implementation Specialist",
        company="Example Corp",
        url="https://jobs.example.test/implementation-specialist",
        description="Help customers configure the product and document requirements.",
        fit_score=78,
    )


def _packet_text(monkeypatch, prefs: dict, profile: UserProfile) -> str:
    monkeypatch.setattr(preferences, "load", lambda path=None: prefs)
    monkeypatch.setattr(
        profile_store,
        "get_profile_store",
        lambda: _FakeProfileStore(profile),
    )
    return "\n".join(packets.prep_result(1, _gig()))


def test_fresh_profile_packet_infers_no_biography_auth_or_work_style(monkeypatch) -> None:
    text = _packet_text(monkeypatch, deepcopy(preferences.DEFAULTS), UserProfile())
    lowered = text.lower()

    unsupported_claims = (
        "ai workflow/rag/automation language matches",
        "python + react",
        "local-first ai portfolio proof",
        "systems background supports",
        "i build practical ai workflow systems",
        "built local-first ai/rag",
        "built react/three.js",
        "built python automation tools",
        "authorized to work in the u.s.",
        "open to remote/hybrid",
        "available for contract discovery",
    )
    assert all(claim not in lowered for claim in unsupported_claims)
    assert preferences.DEFAULTS["identity"]["portfolio"] not in text
    assert preferences.DEFAULTS["background_bullets"]["elevator_pitch"] not in text
    assert "work authorization: yes" not in lowered
    assert "work authorization: no" not in lowered
    assert "requires sponsorship: yes" not in lowered
    assert "requires sponsorship: no" not in lowered
    assert "setup needed: configure work authorization and sponsorship" in lowered
    assert "availability and remote, hybrid, onsite, or relocation preferences are not inferred" in lowered
    assert "no candidate draft was generated from an unconfigured profile" in lowered


def test_packet_includes_only_explicit_synthetic_candidate_facts(monkeypatch) -> None:
    prefs = deepcopy(preferences.DEFAULTS)
    prefs["identity"].update(
        {
            "first_name": "Riley",
            "last_name": "Example",
            "email": "riley@synthetic.test",
            "city": "Testville, ZZ",
            "portfolio": "https://portfolio.synthetic.test",
            "github": "https://github.com/synthetic-riley",
        }
    )
    prefs["links"].update(
        {
            "work_page": "https://portfolio.synthetic.test/case-study",
            "service_page": "https://portfolio.synthetic.test/services",
        }
    )
    prefs["background_bullets"].update(
        {
            "elevator_pitch": "I translate complex customer needs into tested implementations.",
            "field_engineering": "Delivered Project Zephyr for three synthetic customers.",
        }
    )
    profile = UserProfile(authorized_to_work=True, requires_sponsorship=False)

    text = _packet_text(monkeypatch, prefs, profile)

    for configured_fact in (
        "Riley Example",
        "riley@synthetic.test",
        "Testville, ZZ",
        "https://portfolio.synthetic.test/case-study",
        "https://github.com/synthetic-riley",
        "I translate complex customer needs into tested implementations.",
        "Delivered Project Zephyr for three synthetic customers.",
        "Work authorization: Yes (configured in local profile).",
        "Requires sponsorship: No (configured in local profile).",
    ):
        assert configured_fact in text
    assert "Setup needed: work authorization" not in text
