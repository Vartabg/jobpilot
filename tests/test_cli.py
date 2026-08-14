"""CLI smoke tests for JobPilot."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

import jobpilot.cli as cli
from jobpilot.cli import app
from jobpilot.core.application_tracker import ApplicationTracker
from jobpilot.core.jd_parser import ParsedJD
from jobpilot.core.opportunity_ledger import OpportunityLedger
from jobpilot.core.profile_store import UserProfile

runner = CliRunner()


FULL_SCORE_JD = """
Title: Implementation Engineer
Company: Acme
Responsibilities
- Lead customer discovery workshops and product demonstrations.
- Troubleshoot hardware and software integrations at customer sites.
Required qualifications
- Systems troubleshooting experience.
- Clear technical communication with customers.
Preferred qualifications
- Python automation experience.
Work environment
- Work independently through ambiguous technical problems.
Logistics
- Must be authorized to work in the United States without sponsorship.
"""


def _configure_score_evidence(monkeypatch, tmp_path: Path) -> Path:
    accounts_path = tmp_path / "true_accounts.json"
    accounts_path.write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": [
                    {
                        "id": "customer-field",
                        "title": "Customer-site technical work",
                        "summary": (
                            "Diagnosed hardware and software issues at customer sites."
                        ),
                        "details": [
                            "Led customer discovery workshops and product demonstrations.",
                            "Worked independently through ambiguous technical problems.",
                        ],
                        "skills": [
                            "systems troubleshooting",
                            "technical communication",
                        ],
                    }
                ],
            }
        )
    )
    profile = UserProfile(
        skills=["Python"],
        authorized_to_work=True,
        requires_sponsorship=False,
    )
    store = SimpleNamespace(load=lambda: profile)
    monkeypatch.setattr(cli, "get_profile_store", lambda: store)
    monkeypatch.setattr(cli, "TRUE_ACCOUNTS_PATH", accounts_path)
    return accounts_path


def _decision_for_materials() -> cli.RoleDecision:
    return cli.RoleDecision(
        status="assessed",
        decision="stretch",
        role_family="applied_implementation",
        qualification_lower_bound=62,
        work_context_match=75,
        opportunity=80,
        logistics=100,
        evidence_coverage=71,
        biggest_gap="Production Kubernetes experience is not evidenced.",
        matched_accounts=("customer-field",),
        matches=(),
        rationale="Evidence-linked test decision.",
    )


def test_doctor_reports_ready_stack():
    fake_bridge = MagicMock()
    fake_bridge.get_active_page = AsyncMock(return_value=MagicMock())
    fake_bridge.get_page_info = AsyncMock(
        return_value=MagicMock(
            url="https://www.linkedin.com/jobs/view/123",
            title="Senior Frontend Engineer",
            is_linkedin=True,
            is_job_application=True,
        )
    )
    fake_bridge.disconnect = AsyncMock()

    with (
        patch(
            "jobpilot.cli.get_health",
            return_value={
                "status": "ok",
                "whisper": "ready",
                "ollama_models": ["mistral"],
            },
        ),
        patch(
            "jobpilot.cli.connect_to_chrome", new=AsyncMock(return_value=fake_bridge)
        ),
    ):
        result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Chrome CDP" in result.stdout
    assert "AI backend" in result.stdout
    assert "LinkedIn" in result.stdout


def test_doctor_fails_when_chrome_unreachable():
    with (
        patch(
            "jobpilot.cli.get_health",
            return_value={"status": "unreachable", "whisper": "unknown"},
        ),
        patch("jobpilot.cli.connect_to_chrome", new=AsyncMock(return_value=None)),
    ):
        result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "launch_chrome.sh" in result.stdout


def test_start_is_retired_without_invoking_private_engine():
    with patch.object(cli, "_start_async", new=AsyncMock()) as private_start:
        result = runner.invoke(app, ["start"])

    assert result.exit_code == 1
    assert "jobpilot start" in result.stdout
    assert "retired" in result.stdout
    assert "paste" in result.stdout
    assert "submit" in result.stdout
    assert "hand" in result.stdout
    private_start.assert_not_called()


def test_resume_command_generates_output(tmp_path: Path):
    output = tmp_path / "resume.md"
    with patch("jobpilot.core.resume_tailor.OUTPUT_DIR", tmp_path / "resumes"):
        result = runner.invoke(
            app,
            [
                "resume",
                "Senior Frontend Engineer\nAcme AI\nRequirements\n- React\n- TypeScript\n- Remote",
                "--output",
                str(output),
                "--no-bro",
            ],
        )

    assert result.exit_code == 0
    assert output.exists()
    assert output.with_suffix(".html").exists()
    assert "ATS Resume Draft Ready" in result.stdout


def test_resume_output_uses_raw_jd_evidence_not_legacy_fit(tmp_path: Path):
    parsed = ParsedJD(
        title="Implementation Engineer",
        company="Acme",
        raw_text=FULL_SCORE_JD,
    )
    draft = SimpleNamespace(
        fit_result=SimpleNamespace(
            parsed_jd=parsed,
            score=99,
            recommendation="LEGACY APPLY",
        ),
        output_path=tmp_path / "resume.md",
        html_path=tmp_path / "resume.html",
        pdf_path=None,
        keywords=[],
        summary_lines=[],
    )
    tailor = MagicMock()
    tailor.generate_from_text.return_value = draft

    with (
        patch.object(cli, "ResumeTailor", return_value=tailor) as tailor_class,
        patch.object(
            cli,
            "_assess_score_text",
            return_value=_decision_for_materials(),
        ) as assess,
    ):
        result = runner.invoke(app, ["resume", FULL_SCORE_JD])

    assert result.exit_code == 0
    tailor_class.assert_called_once_with(use_bro=False)
    assess.assert_called_once_with(
        title="Implementation Engineer",
        jd_text=FULL_SCORE_JD,
    )
    assert "Role-fit decision: stretch" in result.stdout
    assert "not Apply-ready" in result.stdout
    assert "Qualification floor: 62/100" in result.stdout
    assert "Evidence coverage: 71%" in result.stdout
    assert "Biggest gap:" in result.stdout
    assert "Production Kubernetes experience is not evidenced." in result.stdout
    assert "99/100" not in result.stdout
    assert "LEGACY APPLY" not in result.stdout


def test_prep_output_uses_raw_jd_evidence_not_legacy_fit(tmp_path: Path):
    parsed = ParsedJD(
        title="Implementation Engineer",
        company="Acme",
        raw_text=FULL_SCORE_JD,
    )
    brief = SimpleNamespace(
        fit_result=SimpleNamespace(
            parsed_jd=parsed,
            score=98,
            recommendation="LEGACY APPLY",
        ),
        output_path=tmp_path / "prep.html",
        likely_questions=[],
        gap_notes=[],
    )
    generator = MagicMock()
    generator.generate_from_text.return_value = brief

    with (
        patch.object(cli, "InterviewPrepGenerator", return_value=generator),
        patch.object(
            cli,
            "_assess_score_text",
            return_value=_decision_for_materials(),
        ) as assess,
    ):
        result = runner.invoke(app, ["prep", FULL_SCORE_JD, "--no-bro"])

    assert result.exit_code == 0
    assess.assert_called_once_with(
        title="Implementation Engineer",
        jd_text=FULL_SCORE_JD,
    )
    assert "Role-fit decision: stretch" in result.stdout
    assert "not Apply-ready" in result.stdout
    assert "Qualification floor: 62/100" in result.stdout
    assert "Evidence coverage: 71%" in result.stdout
    assert "Biggest gap:" in result.stdout
    assert "Production Kubernetes experience is not evidenced." in result.stdout
    assert "98/100" not in result.stdout
    assert "LEGACY APPLY" not in result.stdout


def test_score_inline_renders_evidence_decision_not_legacy_fit(
    monkeypatch,
    tmp_path: Path,
):
    _configure_score_evidence(monkeypatch, tmp_path)

    with patch.object(
        cli.JobScorer,
        "score_text",
        side_effect=AssertionError("legacy scorer must not run"),
    ):
        result = runner.invoke(app, ["score", FULL_SCORE_JD])

    assert result.exit_code == 0
    assert "Status: assessed" in result.stdout
    assert "Role-fit decision: apply now" in result.stdout
    assert "not Apply-ready" in result.stdout
    assert "Qualification lower bound" in result.stdout
    assert "Work-context evidence" in result.stdout
    assert "Evidence coverage" in result.stdout
    assert "Opportunity evidence" in result.stdout
    assert "Logistics evidence" in result.stdout
    assert "customer-field" in result.stdout
    assert "Biggest gap" in result.stdout
    assert "Fit Breakdown" not in result.stdout
    assert "psyche" not in result.stdout.lower()
    assert "personality" not in result.stdout.lower()


def test_score_file_keeps_existing_invocation(monkeypatch, tmp_path: Path):
    _configure_score_evidence(monkeypatch, tmp_path)
    jd_path = tmp_path / "role.txt"
    jd_path.write_text(FULL_SCORE_JD)

    result = runner.invoke(app, ["score", str(jd_path)])

    assert result.exit_code == 0
    assert "Implementation Engineer" in result.stdout
    assert "Evidence-linked role decision" in result.stdout
    assert "Status: assessed" in result.stdout


def test_score_missing_jd_is_explicitly_unscorable(monkeypatch, tmp_path: Path):
    _configure_score_evidence(monkeypatch, tmp_path)

    result = runner.invoke(app, ["score", "Implementation Engineer"])

    assert result.exit_code == 0
    assert "Status: unscorable" in result.stdout
    assert "Role-fit decision: investigate" in result.stdout
    assert "Qualification lower bound" in result.stdout
    assert "unknown" in result.stdout
    assert "full job description is required" in result.stdout.lower()
    assert "no neutral points" in result.stdout.lower()


def test_score_active_page_uses_role_decision_engine(monkeypatch, tmp_path: Path):
    _configure_score_evidence(monkeypatch, tmp_path)
    parsed = ParsedJD(
        title="Implementation Engineer",
        company="Acme",
        raw_text=FULL_SCORE_JD,
    )
    parser = MagicMock()
    parser.parse = AsyncMock(return_value=parsed)
    bridge = MagicMock()
    bridge.page = MagicMock()
    bridge.get_active_page = AsyncMock()
    bridge.get_page_info = AsyncMock(
        return_value=SimpleNamespace(
            is_linkedin=True,
            title="Implementation Engineer",
            url="https://www.linkedin.com/jobs/view/123",
        )
    )
    bridge.disconnect = AsyncMock()

    with (
        patch("jobpilot.cli.connect_to_chrome", new=AsyncMock(return_value=bridge)),
        patch("jobpilot.cli.JDParser", return_value=parser),
        patch.object(
            cli.JobScorer,
            "score_parsed_jd",
            side_effect=AssertionError("legacy scorer must not run"),
        ),
    ):
        result = runner.invoke(app, ["score", "--active"])

    assert result.exit_code == 0
    assert "Status: assessed" in result.stdout
    assert "Role-fit decision: apply now" in result.stdout
    assert "not Apply-ready" in result.stdout
    assert "Work-context evidence" in result.stdout
    assert "linkedin.com/jobs/view/123" in result.stdout
    bridge.disconnect.assert_awaited_once()


def test_score_active_page_without_jd_fails_closed(monkeypatch, tmp_path: Path):
    _configure_score_evidence(monkeypatch, tmp_path)
    parser = MagicMock()
    parser.parse = AsyncMock(
        return_value=ParsedJD(
            title="Implementation Engineer",
            company="Acme",
            raw_text="",
        )
    )
    bridge = MagicMock()
    bridge.page = MagicMock()
    bridge.get_active_page = AsyncMock()
    bridge.get_page_info = AsyncMock(
        return_value=SimpleNamespace(
            is_linkedin=True,
            title="Implementation Engineer",
            url="https://www.linkedin.com/jobs/view/123",
        )
    )
    bridge.disconnect = AsyncMock()

    with (
        patch("jobpilot.cli.connect_to_chrome", new=AsyncMock(return_value=bridge)),
        patch("jobpilot.cli.JDParser", return_value=parser),
    ):
        result = runner.invoke(app, ["score", "--active"])

    assert result.exit_code == 0
    assert "Status: unscorable" in result.stdout
    assert "Role-fit decision: investigate" in result.stdout
    assert "full job description is required" in result.stdout.lower()


def test_missing_jd_path_errors_instead_of_treating_as_text(tmp_path: Path):
    """A mistyped JD path must fail loudly, not become the resume's JD text."""
    missing = tmp_path / "does_not_exist.txt"
    result = runner.invoke(app, ["resume", str(missing), "--no-bro"])

    assert result.exit_code == 1
    assert "No such file" in result.stdout


def test_looks_like_path_distinguishes_paths_from_jd_prose():
    # Mistyped paths — should be treated as paths (and error if missing).
    assert cli._looks_like_path("data/jds/entech_access_control.txt")
    assert cli._looks_like_path("resume.md")
    assert cli._looks_like_path("~/jobs/role.txt")
    # Real pasted JD text — multi-word prose, must NOT be read as a path.
    assert not cli._looks_like_path(
        "Field service technician for building automation systems and low voltage wiring"
    )
    assert not cli._looks_like_path("Senior Engineer\nAcme\nRequirements")


def test_profile_list_parser_preserves_order_and_deduplicates():
    assert cli._parse_csv_list(
        "Customer Engineer, Python; Field Service\nPython"
    ) == ["Customer Engineer", "Python", "Field Service"]


def test_optional_yes_no_parser_never_infers_blank_answers():
    assert cli._parse_optional_yes_no("yes") is True
    assert cli._parse_optional_yes_no("NO") is False
    assert cli._parse_optional_yes_no("") is None
    assert cli._parse_optional_yes_no("unknown") is None


def test_profile_edit_prompts_for_country_without_inference():
    profile = UserProfile(city="Austin", state="TX")
    store = SimpleNamespace(load=lambda: profile, save=MagicMock())
    prompts: list[str] = []

    def answer(label: str, *, default="", **_kwargs):
        prompts.append(label)
        if label.startswith("Country"):
            return "Canada"
        return str(default)

    with patch.object(cli.typer, "prompt", side_effect=answer):
        cli._edit_profile(store)

    assert any(label.startswith("Country") for label in prompts)
    assert profile.country == "Canada"
    assert any(label.startswith("Open to relocation") for label in prompts)
    store.save.assert_called_once_with(profile)


def test_profile_edit_keeps_blank_country_unknown():
    profile = UserProfile()
    store = SimpleNamespace(load=lambda: profile, save=MagicMock())

    def keep_default(_label: str, *, default="", **_kwargs):
        return str(default)

    with patch.object(cli.typer, "prompt", side_effect=keep_default):
        cli._edit_profile(store)

    assert profile.country == ""
    store.save.assert_called_once_with(profile)


def test_root_help_has_no_deprecated_psyche_command():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "psyche" not in result.stdout.lower()


def test_board_command_renders_dashboard():
    with patch("jobpilot.ui.terminal_board.render_board") as render:
        result = runner.invoke(app, ["board", "--austin", "--limit", "5"])

    assert result.exit_code == 0
    render.assert_called_once()


def test_apply_claim_lock_blocks_non_ready_target(tmp_path: Path):
    claim_file = tmp_path / "claude-vetted-targets.json"
    claim_file.write_text(json.dumps({
        "targets": [{
            "company": "Huntress",
            "title": "Forward Deployed Engineer",
            "decision": "keep",
            "materials_status": "claude-preparing",
        }]
    }))
    job = SimpleNamespace(
        id="abc123",
        company="Huntress",
        title="Forward Deployed Engineer",
        url="https://job-boards.greenhouse.io/huntress/jobs/7711271003",
        location="Remote US",
        track="both",
        fit_score=78,
        status="queued",
        decision="apply_now",
        assessment_status="assessed",
        legitimacy_state="recommend",
        evidence_grade="A",
        qualification_lower_bound=78,
        evidence_coverage=80,
    )

    with (
        patch.object(cli, "CLAUDE_VETTED_TARGETS_PATH", claim_file),
        patch("jobpilot.core.queue_builder.get_job", return_value=job),
        patch("jobpilot.core.queue_builder.is_apply_ready", return_value=True),
        patch(
            "jobpilot.core.queue_builder.has_current_action_provenance",
            return_value=True,
        ),
    ):
        result = runner.invoke(app, ["apply", "abc123"])

    assert result.exit_code == 1
    assert "Claim-lock blocked staging" in result.stdout


def test_apply_command_never_automates_the_live_ats_form():
    job = SimpleNamespace(
        id="abc123",
        company="Acme",
        title="Customer Engineer",
        url="https://jobs.example.test/acme/1",
        location="Austin",
        track="both",
        fit_score=78,
        status="queued",
        decision="apply_now",
        assessment_status="assessed",
        legitimacy_state="recommend",
        evidence_grade="A",
        qualification_lower_bound=78,
        evidence_coverage=80,
    )

    with (
        patch("jobpilot.core.queue_builder.get_job", return_value=job),
        patch("jobpilot.core.queue_builder.is_apply_ready", return_value=True),
        patch(
            "jobpilot.core.queue_builder.has_current_action_provenance",
            return_value=True,
        ),
        patch.object(cli, "_enforce_claim_lock"),
        patch("jobpilot.core.form_filler.fill_application", new=AsyncMock()) as fill,
    ):
        result = runner.invoke(app, ["apply", "abc123"])

    assert result.exit_code == 0
    assert "paste" in result.stdout.lower()
    fill.assert_not_awaited()


def test_apply_command_blocks_orphaned_cached_recommendation():
    job = SimpleNamespace(
        id="abc123",
        company="Acme",
        title="Customer Engineer",
        url="https://jobs.ashbyhq.com/acme/role-1",
        location="Austin",
        portal="ashby",
        provider_job_id="role-1",
        track="both",
        fit_score=78,
        status="queued",
        decision="apply_now",
        assessment_status="assessed",
        legitimacy_state="recommend",
        evidence_grade="A",
        qualification_lower_bound=78,
        evidence_coverage=80,
    )

    with (
        patch("jobpilot.core.queue_builder.get_job", return_value=job),
        patch("jobpilot.core.queue_builder.is_apply_ready", return_value=True),
        patch(
            "jobpilot.core.queue_builder.has_current_action_provenance",
            return_value=False,
        ),
        patch.object(cli, "_enforce_claim_lock") as claim_lock,
    ):
        result = runner.invoke(app, ["apply", "abc123"])

    assert result.exit_code == 1
    assert "not apply-ready" in result.stdout
    claim_lock.assert_not_called()


def test_log_records_explicit_outcome_in_ledger(monkeypatch, tmp_path: Path):
    tracker = ApplicationTracker(data_dir=tmp_path / "tracker")
    ledger_path = tmp_path / "opportunities.db"
    monkeypatch.setattr(cli, "get_application_tracker", lambda: tracker)
    monkeypatch.setattr(
        "jobpilot.core.opportunity_ledger.OpportunityLedger",
        lambda: OpportunityLedger(db_path=ledger_path),
    )

    result = runner.invoke(
        app,
        ["log", "Acme", "--title", "Customer Engineer", "--status", "human_reply"],
    )

    assert result.exit_code == 0
    ledger = OpportunityLedger(db_path=ledger_path)
    try:
        events = ledger.iter_events(event_type="human_reply")
        assert len(events) == 1
        assert events[0].source == "manual-log"
        assert events[0].provenance["acquisition_channel"] == "unknown"
    finally:
        ledger.close()
