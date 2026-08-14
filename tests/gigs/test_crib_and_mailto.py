"""Tests for the prefilled-mailto path and the crib sheet generator."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from jobpilot.gigs.core import preferences
from jobpilot.gigs.core.crib import mobile_crib, write_crib_sheet
from jobpilot.gigs.core.dispatcher import _apply_target, _build_actions
from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.proposals import email_body, email_subject


def _gig(**overrides) -> Gig:
    base = dict(
        id="hn-1",
        source="hn",
        title="Acme AI | Senior Engineer | NYC",
        url="https://news.ycombinator.com/item?id=1",
        apply_url="mailto:jobs@acme.ai",
        company="Acme AI",
        description="LLM workflow automation in Python.",
        location="NYC",
        salary_min=180000,
        salary_max=200000,
        fit_score=95,
        tags=["ai", "python", "agent"],
    )
    base.update(overrides)
    return Gig(**base)


def test_apply_target_prefills_mailto_with_subject_and_body() -> None:
    g = _gig()
    target = _apply_target(g)
    assert target.startswith("mailto:jobs@acme.ai?")
    qs = parse_qs(urlparse(target).query)
    assert "subject" in qs
    assert "body" in qs
    # Subject should reflect the role
    assert "Acme AI" in qs["subject"][0] or "Senior Engineer" in qs["subject"][0]
    # Body should be the email-ready draft (no review note)
    assert "Review before sending" not in qs["body"][0]
    assert "Hi -" in qs["body"][0]


def test_apply_target_passes_through_non_mailto_unchanged() -> None:
    g = _gig(apply_url="https://boards.greenhouse.io/acme/jobs/123")
    assert _apply_target(g) == "https://boards.greenhouse.io/acme/jobs/123"


def test_apply_target_falls_back_to_url_when_no_apply_url() -> None:
    g = _gig(apply_url="")
    assert _apply_target(g) == "https://news.ycombinator.com/item?id=1"


def test_email_body_strips_review_footer() -> None:
    g = _gig()
    body = email_body(g)
    assert "Review before sending" not in body
    # Now ends with signoff (name + phone + linkedin/portfolio), not the offer body
    ident = preferences.identity()
    assert f"Best,\n{ident['first_name']} {ident['last_name']}" in body
    assert ident["portfolio"] in body


def test_email_subject_includes_role() -> None:
    g = _gig()
    subject = email_subject(g)
    assert "Acme AI" in subject
    assert len(subject) <= 130


def test_email_subject_handles_prose_only_post() -> None:
    """When a gig has no clean role line (HN prose-only post),
    email subject should still be short and not paste a paragraph."""
    g = _gig(
        company="",
        title="By turning legal code into AI code, Norm enables enterprises to move faster across compliance and legal review",
    )
    subject = email_subject(g)
    assert len(subject) <= 110
    # Should not contain the long sentence in full
    assert "compliance and legal review" not in subject


def test_email_body_uses_short_role_for_prose_posts() -> None:
    g = _gig(
        company="Norm AI",
        title="By turning legal code into AI code, Norm enables enterprises to move faster across compliance and legal review",
    )
    body = email_body(g)
    # Should reference 'Norm AI role' rather than paste the marketing paragraph
    assert "compliance and legal review" not in body
    assert "Norm AI" in body


def test_build_actions_single_apply_button() -> None:
    """ntfy actions are now a single Apply button — the noisy 'View post'
    secondary action was removed in the simplification refactor."""
    g = _gig(apply_url="mailto:jobs@acme.ai")
    actions = _build_actions(g)
    assert actions.count("view, ") == 1
    assert "View role" in actions
    assert g.url in actions
    assert "mailto:" not in actions
    assert "View post" not in actions


def test_crib_sheet_has_standard_answers_and_per_lead_section(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        preferences,
        "load",
        lambda path=None: deepcopy(preferences.DEFAULTS),
    )
    neutral_profile = SimpleNamespace(
        country="",
        authorized_to_work=None,
        requires_sponsorship=None,
        demographics={},
    )
    monkeypatch.setattr(
        "jobpilot.core.profile_store.get_profile_store",
        lambda: SimpleNamespace(load=lambda: neutral_profile),
    )
    crib = write_crib_sheet([_gig()], crib_dir=tmp_path)
    text = crib.read_text()
    # Identity block — email comes from preferences (DEFAULTS or
    # data/preferences.json), never hardcoded here.
    assert preferences.identity()["email"] in text
    # Standard answers remain truth-bounded: unset authorization is omitted.
    assert "Authorized to work in the US?" not in text
    assert "Veteran status" in text
    # Per-lead section: no inferred salary assent; employer range is private context.
    assert "Acme AI" in text
    assert "Desired salary (paste)" not in text
    assert "Review and answer manually" in text
    assert "Your anchor (don't paste)" in text
    # Candidate-facing band references the stated 180-200K band
    assert "K" in text and "180" in text and "200" in text
    # ...but must NOT leak the anchoring strategy into the pasteable field
    assert "just under" not in text.split("Your anchor")[0]
    # Resume hard-line is preserved
    assert "Resume" in text and "manual" in text


def test_crib_sheet_relocate_answer_flips_for_non_home_metro_role(
    tmp_path: Path, monkeypatch
) -> None:
    # Home metro comes from preferences (empty by default — neutral defaults
    # ship no location). Simulate a user whose home metro is New York.
    prefs = preferences.load()
    prefs["location"] = {"home_metro_tags": ["new york", "nyc"]}
    prefs["work_style"] = {
        **preferences.DEFAULTS["work_style"],
        "relocate_default": "Open to relocation",
    }
    monkeypatch.setattr(preferences, "load", lambda path=None: prefs)

    home = _gig(location="New York, NY")
    elsewhere = _gig(
        id="hn-2",
        title="Acme AI | Senior Engineer | Berlin",
        location="Berlin, Germany",
        description="Remote.",
    )
    text_home = write_crib_sheet([home], crib_dir=tmp_path / "home").read_text()
    text_other = write_crib_sheet([elsewhere], crib_dir=tmp_path / "other").read_text()
    # Home-metro roles get a "local to this role" note appended to the
    # data-defined relocate answer; non-home roles get the base answer only.
    assert "local to this role" in text_home
    assert "local to this role" not in text_other


def test_role_is_not_local_from_headquarters_mentioned_in_description(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prefs = preferences.load()
    prefs["location"] = {"home_metro_tags": ["austin"]}
    prefs["work_style"] = {
        **preferences.DEFAULTS["work_style"],
        "relocate_default": "Based in Austin; open to relocation",
    }
    monkeypatch.setattr(preferences, "load", lambda path=None: prefs)
    berlin = _gig(
        location="Berlin, Germany",
        title="Austin company — Engineer",
        description="Headquartered in Austin; this role is based in Berlin.",
    )

    text = write_crib_sheet([berlin], crib_dir=tmp_path).read_text()

    assert "Based in Austin; open to relocation — local to this role" not in text


def test_crib_sheet_relocate_answer_is_data_driven(tmp_path: Path, monkeypatch) -> None:
    # The relocate answer comes from preferences work_style — never hardcoded.
    prefs = dict(preferences.DEFAULTS)
    prefs["work_style"] = {"relocate_default": "Based in Testville", "in_office_default": "x"}
    monkeypatch.setattr(preferences, "load", lambda path=None: prefs)
    g = _gig(location="New York, NY")
    text = write_crib_sheet([g], crib_dir=tmp_path).read_text()
    assert "Based in Testville" in text


def test_crib_sheet_handles_no_pay_band_without_inventing_response(tmp_path: Path) -> None:
    g = _gig(salary_min=0, salary_max=0, pay_hourly_est=0)
    text = write_crib_sheet([g], crib_dir=tmp_path).read_text()
    assert "Review and answer manually" in text
    assert "happy to discuss range" not in text


def test_crib_uses_exact_explicit_candidate_salary_response(tmp_path: Path, monkeypatch) -> None:
    prefs = preferences.load()
    prefs["pay"] = {**prefs["pay"], "candidate_response": "My target base is $160,000."}
    monkeypatch.setattr(preferences, "load", lambda path=None: prefs)

    crib = mobile_crib(_gig())

    assert crib["salary_paste"] == "My target base is $160,000."


def test_fresh_profile_crib_has_no_inferred_sensitive_answers(monkeypatch) -> None:
    from jobpilot.core import profile_store
    from jobpilot.core.profile_store import UserProfile

    monkeypatch.setattr(
        profile_store,
        "get_profile_store",
        lambda: type("Store", (), {"load": lambda self: UserProfile()})(),
    )
    prefs = preferences.load()
    prefs["work_style"] = dict(preferences.DEFAULTS["work_style"])
    monkeypatch.setattr(preferences, "load", lambda path=None: prefs)

    crib = mobile_crib(_gig())
    answers = {row["label"]: row["value"] for row in crib["ats_answers"]}

    assert "Authorized to work in the US?" not in answers
    assert "Require visa sponsorship?" not in answers
    assert "Privacy consent" not in answers
    assert "How did you hear about us?" not in answers
    assert crib["relocate"] == ""
