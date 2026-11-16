import pytest

from jobpilot.engine.domain import (
    ChannelKind,
    EmployerRecord,
    FormSnapshot,
    FrictionTier,
    GateKind,
    HrContact,
    OutreachSender,
    QueueRoute,
    RequirementCategory,
    RequirementObservation,
    TargetLane,
    build_action_queue,
    classify_requirement,
    classify_target_lane,
    draft_recruiter_outreach,
    extract_hr_contact,
    infer_ats_provider_and_gate,
    normalize_employer_key,
    redact_sensitive_text,
    sanitize_role_url,
    summarize_employer,
)
from jobpilot.engine.domain.opportunity import Lane, Opportunity


def test_normalize_employer_key_preserves_distinct_brands():
    assert normalize_employer_key("Acme, Inc.") == "acme"
    assert normalize_employer_key("Team Junction") == "junction"
    assert normalize_employer_key("Stuut Hiring Team") == "stuut"
    assert normalize_employer_key("Avallon AI") == "avallon ai"
    assert normalize_employer_key("6sense") == "6sense"
    assert normalize_employer_key("8VC") == "8vc"
    assert normalize_employer_key("   ") == ""


def test_sanitize_role_url_and_provider_gate_inference():
    assert (
        sanitize_role_url(
            "https://boards.greenhouse.io/acme/jobs/123?gh_src=li&token=secret#app"
        )
        == "https://boards.greenhouse.io/acme/jobs/123"
    )
    assert (
        sanitize_role_url(
            "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=555&gh_src=x"
        )
        == "https://boards.greenhouse.io/embed/job_app?for=acme&token=555"
    )
    prov, ten, _jid, gate = infer_ats_provider_and_gate(
        "https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/Austin/Tech_R123/apply"
    )
    assert prov == "workday"
    assert ten == "acme"
    assert gate is GateKind.ACCOUNT_REQUIRED

    prov2, ten2, _, gate2 = infer_ats_provider_and_gate(
        "https://ats.rippling.com/acme-co/jobs/uuid-1"
    )
    assert prov2 == "rippling"
    assert ten2 == "acme-co"
    assert gate2 is GateKind.CONSENT_WALL


def test_redact_sensitive_text_preserves_prose_and_scrubs_otps():
    assert (
        redact_sensitive_text("Your Pinpoint application was received")
        == "Your Pinpoint application was received"
    )
    assert (
        redact_sensitive_text("Share your opinions with us")
        == "Share your opinions with us"
    )
    assert redact_sensitive_text("Security code from Acme") == "Security code from Acme"
    assert (
        redact_sensitive_text("Security code: jebUzw3M")
        == "Security code: [redacted-code]"
    )
    assert (
        redact_sensitive_text("Your verification code is 849201")
        == "Your verification code is [redacted-code]"
    )
    assert (
        redact_sensitive_text("Contact hr@example.com or 555-010-9999")
        == "Contact [redacted-email] or [redacted-phone]"
    )


def test_classify_requirement_and_summarize_employer():
    cat_std, gate_std, beyond_std = classify_requirement(
        "First Name", "string", "required"
    )
    assert (cat_std, gate_std, beyond_std) == (
        RequirementCategory.IDENTITY,
        None,
        False,
    )

    cat_eeo, gate_eeo, beyond_eeo = classify_requirement("Gender", "select", "optional")
    assert (cat_eeo, gate_eeo, beyond_eeo) == (
        RequirementCategory.DEMOGRAPHICS_EEO,
        None,
        False,
    )

    cat_sal, gate_sal, beyond_sal = classify_requirement(
        "Desired annual salary", "number", "required"
    )
    assert (cat_sal, gate_sal, beyond_sal) == (
        RequirementCategory.COMPENSATION,
        None,
        True,
    )

    cat_cap, gate_cap, beyond_cap = classify_requirement(
        "Solved interactive hCaptcha puzzle", "", "required"
    )
    assert (cat_cap, gate_cap, beyond_cap) == (
        RequirementCategory.OTHER,
        GateKind.CAPTCHA,
        True,
    )

    emp = EmployerRecord(
        "acme", "Acme", providers=("ashby",), board_tokens=("ashby:acme",)
    )
    obs_custom = RequirementObservation(
        employer_key="acme",
        employer_name="Acme",
        role_key="ashby:acme:1",
        category=cat_sal,
        label="Desired annual salary",
        requiredness="required",
        is_beyond_standard=beyond_sal,
        source_kind="test",
    )
    snap_ok = FormSnapshot(
        "ashby:acme:1",
        "acme",
        "ashby",
        "Eng",
        "https://jobs.ashbyhq.com/acme/1",
        "ok",
        field_count=5,
        beyond_standard_count=1,
    )
    summary_custom = summarize_employer(emp, (), [obs_custom], [snap_ok])
    assert summary_custom.friction_tier is FrictionTier.CUSTOM_QUESTIONS
    assert summary_custom.gates == (GateKind.NONE,)

    obs_gate = RequirementObservation(
        employer_key="acme",
        employer_name="Acme",
        role_key="ashby:acme:1",
        category=RequirementCategory.OTHER,
        label="Captcha",
        requiredness="required",
        gate_kind=GateKind.CAPTCHA,
        is_beyond_standard=True,
        source_kind="test",
    )
    summary_gated = summarize_employer(emp, (), [obs_custom, obs_gate], [snap_ok])
    assert summary_gated.friction_tier is FrictionTier.GATED
    assert summary_gated.gates == (GateKind.CAPTCHA,)


def test_extract_hr_contact_and_greenhouse_security_code():
    cand = frozenset({"applicant@example.com"})
    skipped, gate0, emp0 = extract_hr_contact(
        "Applicant <applicant@example.com>",
        subject="Re: Interview",
        candidate_emails=cand,
    )
    assert skipped is None and gate0 is None and emp0 == ""

    direct, gate1, emp1 = extract_hr_contact(
        "Jordan Lee <jordan@canopy.example.com>",
        subject="Update on Your Application to Canopy",
        date_iso="2026-11-14T18:56:00+00:00",
        candidate_emails=cand,
    )
    assert direct is not None
    assert direct.channel_kind is ChannelKind.DIRECT_PERSON
    assert emp1 == "canopy"
    assert gate1 is None

    team, gate2, emp2 = extract_hr_contact(
        "Careers <careers@acme.example.com>",
        subject="Thanks for applying to Acme",
        candidate_emails=cand,
    )
    assert team is not None and team.channel_kind is ChannelKind.COMPANY_RECRUITING
    assert emp2 == "acme" and gate2 is None

    sec, gate3, emp3 = extract_hr_contact(
        "no-reply@greenhouse.io",
        subject="Security code for your application to Pinpoint Labs",
        candidate_emails=cand,
    )
    assert sec is not None and sec.channel_kind is ChannelKind.ATS_RELAY
    assert gate3 is GateKind.EMAIL_VERIFICATION_CODE
    assert emp3 == "pinpoint labs"
    assert "Pinpoint" in sec.sample_subject

    with pytest.raises(ValueError):
        EmployerRecord("   ", "Blank")


def test_unbracketed_header_replyable_and_action_queue_routing():
    cand = frozenset({"applicant@example.com"})
    unbr, _, emp_u = extract_hr_contact(
        '"BetaCo" no-reply@careers.beta.example.com',
        subject="Thank you for applying",
        candidate_emails=cand,
    )
    assert unbr is not None
    assert unbr.email == "no-reply@careers.beta.example.com"
    assert unbr.is_replyable is False
    assert emp_u == "betaco"

    bot = HrContact(
        "asml",
        "rex@asml.example.com",
        "Rex from ASML",
        channel_kind=ChannelKind.DIRECT_PERSON,
    )
    assert bot.is_replyable is False

    human = HrContact(
        "kla",
        "sarah.recruiter@kla.example.com",
        "Recruiter, Sarah",
        channel_kind=ChannelKind.DIRECT_PERSON,
    )
    assert human.is_replyable is True

    assert classify_target_lane("Sales Development Representative") == (None, 0, "")
    assert classify_target_lane("Senior Staff Engineer") == (None, 0, "")
    assert (
        classify_target_lane("Tech Support Engineer")[0] is TargetLane.TECHNICAL_SUPPORT
    )
    assert (
        classify_target_lane("Field Service Engineer")[0] is TargetLane.FIELD_HARDWARE
    )

    kla_emp = EmployerRecord("kla", "KLA", providers=("workday",))
    kla_obs = RequirementObservation(
        "kla",
        "KLA",
        "workday:kla:1",
        gate_kind=GateKind.ACCOUNT_REQUIRED,
        is_beyond_standard=True,
        source_kind="status",
    )
    kla_sum = summarize_employer(kla_emp, [human], [kla_obs], [])

    acme_emp = EmployerRecord(
        "acme", "Acme", providers=("ashby",), board_tokens=("ashby:acme",)
    )
    acme_snap = FormSnapshot(
        "ashby:acme:1",
        "acme",
        "ashby",
        "FDE",
        "https://jobs.ashbyhq.com/acme/1",
        "ok",
        field_count=4,
    )
    acme_sum = summarize_employer(acme_emp, [], [], [acme_snap])

    opps = [
        Opportunity(
            "1",
            "k1",
            Lane.JOB,
            "KLA",
            "Tech Support Engineer",
            "https://kla.wd1.myworkdayjobs.com/Search/job/Austin/TSE_1/apply",
            "Austin, TX",
            "workday",
            "kla",
            "1",
            "",
            "",
        ),
        Opportunity(
            "2",
            "k2",
            Lane.JOB,
            "Acme",
            "Forward Deployed Engineer",
            "https://jobs.ashbyhq.com/acme/1",
            "Remote - US",
            "ashby",
            "acme",
            "1",
            "",
            "",
        ),
        Opportunity(
            "3",
            "k3",
            Lane.JOB,
            "Acme",
            "Product Engineer",
            "https://jobs.ashbyhq.com/acme/2",
            "Remote - US",
            "ashby",
            "acme",
            "2",
            "",
            "",
        ),
        Opportunity(
            "4",
            "k4",
            Lane.JOB,
            "Acme",
            "Sales Development Representative",
            "https://jobs.ashbyhq.com/acme/3",
            "Remote - US",
            "ashby",
            "acme",
            "3",
            "",
            "",
        ),
    ]
    queue = build_action_queue(opps, {"kla": kla_sum, "acme": acme_sum})
    assert len(queue) == 2
    bypass = [q for q in queue if q.route is QueueRoute.RECRUITER_BYPASS]
    ready = [q for q in queue if q.route is QueueRoute.AUTO_APPLY_READY]
    assert len(bypass) == 1 and bypass[0].company == "KLA"
    assert "Hello Sarah," in bypass[0].outreach_body
    assert "years" not in bypass[0].outreach_body.lower()
    # No sender passed: the draft carries no contact details or location.
    assert "@" not in bypass[0].outreach_body
    assert "based in" not in bypass[0].outreach_body
    assert len(ready) == 1 and ready[0].title == "Forward Deployed Engineer"


def test_outreach_signature_and_location_come_from_sender():
    contact = HrContact(
        "acme",
        "pat.recruiter@acme.example.com",
        "Pat Recruiter",
        channel_kind=ChannelKind.DIRECT_PERSON,
    )
    sender = OutreachSender(
        name="Ada Applicant",
        contact_line="555-0100 | applicant@example.com",
        availability="based in Springfield, IL",
    )
    subject, body = draft_recruiter_outreach(
        "Acme", "Support Engineer", TargetLane.TECHNICAL_SUPPORT, contact, sender
    )
    assert subject == "Application follow-up: Support Engineer — Ada Applicant"
    assert "introductory conversation (based in Springfield, IL).\n\n" in body
    assert body.endswith("Thank you,\nAda Applicant\n555-0100 | applicant@example.com")

    bare_subject, bare_body = draft_recruiter_outreach(
        "Acme", "Support Engineer", TargetLane.TECHNICAL_SUPPORT, contact
    )
    assert bare_subject == "Application follow-up: Support Engineer"
    assert "introductory conversation.\n\n" in bare_body
    assert bare_body.endswith("Thank you,")
