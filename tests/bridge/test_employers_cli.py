import csv
import json
import os
import sqlite3

from typer.testing import CliRunner

from jobpilot.bridge import employers_cli
from jobpilot.engine.domain import (
    BoardResult,
    BoardTarget,
    FormSnapshot,
    GateKind,
    Lane,
    Listing,
    Posting,
    RequirementCategory,
    RequirementObservation,
)


def _setup_fixture_dirs(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "portals.json").write_text(
        json.dumps(
            [
                {
                    "portal": "greenhouse",
                    "value": "acme",
                    "label": "Acme",
                    "enabled": True,
                }
            ]
        )
    )
    (data_dir / "profile.json").write_text(
        json.dumps(
            {
                "first_name": "Ada",
                "last_name": "Applicant",
                "email": "applicant@example.com",
                "phone": "555-0100",
                "city": "Springfield",
                "state": "IL",
                "open_to_relocation": False,
            }
        )
    )

    with sqlite3.connect(data_dir / "applications.db") as db:
        db.execute(
            "CREATE TABLE applications (id INTEGER PRIMARY KEY, job_url TEXT, job_title TEXT, "
            "company TEXT, applied_at TEXT, status TEXT, step_reached INTEGER, fields_filled INTEGER, updated_at TEXT)"
        )
        db.execute(
            "INSERT INTO applications (job_url, job_title, company, applied_at, status, updated_at) "
            "VALUES ('https://acme.wd1.myworkdayjobs.com/Careers/job/Tech_R1/apply', 'Tech', 'Acme Workday', '2026-11-10', 'abandoned', '2026-11-10T10:00:00')"
        )

    js = tmp_path / "job-search"
    nyc = js / "nyc-100-2026-11-11"
    nyc.mkdir(parents=True)
    (nyc / "form-preflight.json").write_text(
        json.dumps(
            [
                {
                    "campaign_id": "001",
                    "company": "Beta Co",
                    "title": "=SUM(1,2) Support Engineer",
                    "apply_url": "https://jobs.ashbyhq.com/betaco/uuid-1/application",
                    "provider": "ashby",
                    "required": [
                        {"label": "Email", "type": "Email"},
                        {"label": "Desired salary", "type": "Number"},
                    ],
                    "possible_legal_or_policy_questions": [],
                    "possible_additional_information": [],
                }
            ]
        )
    )
    (nyc / "STATUS.md").write_text(
        "| # | Employer and role | Outcome | Receipt or blocker |\n"
        "| --- | --- | --- | --- |\n"
        "| 002 | [Gamma Corp · Tech](https://boards.greenhouse.io/gamma/jobs/42) | Verification required | Emailed 8-character security code pending |\n"
    )

    gmail_jsonl = tmp_path / "gmail.jsonl"
    gmail_jsonl.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "m1",
                        "payload": {
                            "headers": [
                                {
                                    "name": "From",
                                    "value": "Applicant <applicant@example.com>",
                                },
                                {"name": "Subject", "value": "Re: Hello"},
                                {
                                    "name": "Date",
                                    "value": "Tue, 10 Nov 2026 12:00:00 +0000",
                                },
                            ]
                        },
                        "snippet": "Should never be imported",
                    }
                ),
                json.dumps(
                    {
                        "id": "m2",
                        "payload": {
                            "headers": [
                                {
                                    "name": "From",
                                    "value": "Dana <dana@betaco.example.com>",
                                },
                                {
                                    "name": "Subject",
                                    "value": "Update on Your Application to Beta Co",
                                },
                                {
                                    "name": "Date",
                                    "value": "Wed, 11 Nov 2026 14:00:00 +0000",
                                },
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "id": "m3",
                        "payload": {
                            "headers": [
                                {"name": "From", "value": "no-reply@greenhouse.io"},
                                {
                                    "name": "Subject",
                                    "value": "Security code for your application to Gamma Corp",
                                },
                                {
                                    "name": "Date",
                                    "value": "Wed, 11 Nov 2026 15:00:00 +0000",
                                },
                            ]
                        },
                    }
                ),
            ]
        )
    )
    return data_dir, js, gmail_jsonl


def test_employers_cli_import_collect_status_list_show_export(tmp_path, monkeypatch):
    runner = CliRunner()
    data_dir, js, gmail_jsonl = _setup_fixture_dirs(tmp_path)
    store_path = tmp_path / "employers.db"
    out_dir = tmp_path / "export"

    # Dry-run import leaves store absent
    dry = runner.invoke(
        employers_cli.app,
        [
            "import",
            "--dry-run",
            "--store",
            str(store_path),
            "--data-dir",
            str(data_dir),
            "--job-search-dir",
            str(js),
            "--gmail-jsonl",
            str(gmail_jsonl),
        ],
    )
    assert dry.exit_code == 0, dry.stdout
    assert not store_path.exists()

    # Real import + idempotent re-import
    res = runner.invoke(
        employers_cli.app,
        [
            "import",
            "--store",
            str(store_path),
            "--data-dir",
            str(data_dir),
            "--job-search-dir",
            str(js),
            "--gmail-jsonl",
            str(gmail_jsonl),
        ],
    )
    assert res.exit_code == 0, res.stdout
    assert store_path.exists()

    # Collect with mocked Boards and FormSchemas
    class FakeBoards:
        def fetch(self, target: BoardTarget) -> BoardResult:
            return BoardResult(
                target,
                (
                    Posting(
                        Listing(
                            company="Acme",
                            title="Field Engineer",
                            url="https://boards.greenhouse.io/acme/jobs/777",
                            lane=Lane.JOB,
                            provider="greenhouse",
                            tenant="acme",
                            provider_job_id="777",
                        )
                    ),
                ),
            )

    class FakeFetcher:
        def fetch(self, opp):
            snap = FormSnapshot(
                role_key="greenhouse:acme:777",
                employer_key="acme",
                provider="greenhouse",
                role_title=opp.title,
                role_url=opp.url,
                status="ok",
                field_count=2,
                beyond_standard_count=0,
                gates=(GateKind.NONE,),
                fetched_at="2026-11-16T12:00:00+00:00",
            )
            obs = (
                RequirementObservation(
                    employer_key="acme",
                    employer_name="Acme",
                    role_key="greenhouse:acme:777",
                    role_title=opp.title,
                    role_url=opp.url,
                    provider="greenhouse",
                    category=RequirementCategory.CONTACT,
                    label="Email",
                    requiredness="required",
                    is_beyond_standard=False,
                    source_kind="live_ats_form",
                ),
            )
            return snap, obs

    monkeypatch.setattr(employers_cli, "_make_boards", lambda: FakeBoards())
    monkeypatch.setattr(
        employers_cli, "_make_fetcher", lambda delay_ms=0: FakeFetcher()
    )

    col = runner.invoke(
        employers_cli.app,
        [
            "collect",
            "--store",
            str(store_path),
            "--ledger",
            str(data_dir / "opportunities.db"),
            "--portals",
            str(data_dir / "portals.json"),
        ],
    )
    assert col.exit_code == 0, col.stdout

    st = runner.invoke(employers_cli.app, ["status", "--store", str(store_path)])
    assert st.exit_code == 0, st.stdout
    assert "Employer Intelligence Overview" in st.stdout

    lst = runner.invoke(
        employers_cli.app, ["list", "--store", str(store_path), "--has-direct-hr"]
    )
    assert lst.exit_code == 0 and "Beta Co" in lst.stdout

    shw = runner.invoke(
        employers_cli.app, ["show", "Beta Co", "--store", str(store_path)]
    )
    assert shw.exit_code == 0 and "dana@betaco.example.com" in shw.stdout

    exp = runner.invoke(
        employers_cli.app,
        ["export", "--store", str(store_path), "--out-dir", str(out_dir)],
    )
    assert exp.exit_code == 0, exp.stdout
    for fname in (
        "EMPLOYERS.md",
        "employers.json",
        "requirements.csv",
        "hr-contacts.csv",
    ):
        fpath = out_dir / fname
        assert fpath.exists()
        if os.name == "posix":
            assert (fpath.stat().st_mode & 0o777) == 0o600

    # Verify applicant email is excluded and CSV formula injection is neutralized
    hr_text = (out_dir / "hr-contacts.csv").read_text(encoding="utf-8")
    assert "applicant@example.com" not in hr_text
    assert "dana@betaco.example.com" in hr_text

    with (out_dir / "requirements.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    titles = [r["role_title"] for r in rows]
    assert any(t.startswith("'=SUM(1,2)") for t in titles)

    # Test collect --one-per-employer --from-store-boards + plan-queue
    col2 = runner.invoke(
        employers_cli.app,
        [
            "collect",
            "--store",
            str(store_path),
            "--ledger",
            str(data_dir / "opportunities.db"),
            "--portals",
            str(data_dir / "portals.json"),
            "--one-per-employer",
            "--from-store-boards",
            "--refresh-existing",
        ],
    )
    assert col2.exit_code == 0, col2.stdout

    pq = runner.invoke(
        employers_cli.app,
        [
            "plan-queue",
            "--store",
            str(store_path),
            "--ledger",
            str(data_dir / "opportunities.db"),
            "--status-md",
            str(js / "nyc-100-2026-11-11" / "STATUS.md"),
            "--tracker",
            str(data_dir / "applications.db"),
            "--applicant-profile",
            str(data_dir / "profile.json"),
            "--out-dir",
            str(out_dir),
        ],
    )
    assert pq.exit_code == 0, pq.stdout
    for fname in (
        "standing-policy.json",
        "action-queue.json",
        "ACTION-QUEUE.md",
        "LINKEDIN-PART2-BRIEF.md",
    ):
        fpath = out_dir / fname
        assert fpath.exists()
        if os.name == "posix":
            assert (fpath.stat().st_mode & 0o777) == 0o600
    assert "Link" in (out_dir / "ACTION-QUEUE.md").read_text(
        encoding="utf-8"
    ) or "Action Queue" in (out_dir / "ACTION-QUEUE.md").read_text(encoding="utf-8")
    assert "applicant@example.com" not in (out_dir / "standing-policy.json").read_text(
        encoding="utf-8"
    )
    policy = json.loads((out_dir / "standing-policy.json").read_text(encoding="utf-8"))
    loc = policy["rules"]["location_schedule"]
    assert loc["current_city_state"] == "Springfield, IL"
    assert loc["policy"] == "not open to relocation"
    assert "555-0100" not in json.dumps(policy)


def test_outreach_sender_reads_private_profile(tmp_path):
    prof = tmp_path / "profile.json"
    prof.write_text(
        json.dumps(
            {
                "first_name": "Ada",
                "last_name": "Applicant",
                "email": "applicant@example.com",
                "phone": "555-0100",
                "city": "Springfield",
                "state": "IL",
                "open_to_relocation": True,
            }
        )
    )
    sender = employers_cli._outreach_sender(employers_cli._read_private_profile(prof))
    assert sender.name == "Ada Applicant"
    assert sender.contact_line == "555-0100 | applicant@example.com"
    assert sender.availability == "based in Springfield, IL; open to relocation"

    prof.write_text(json.dumps({"outreach_availability": "remote or on-site"}))
    sender = employers_cli._outreach_sender(employers_cli._read_private_profile(prof))
    assert sender == employers_cli.OutreachSender(availability="remote or on-site")

    missing = employers_cli._read_private_profile(tmp_path / "absent.json")
    assert missing == {}
    assert employers_cli._outreach_sender(missing) == employers_cli.OutreachSender()


def test_import_exits_nonzero_on_missing_explicit_gmail_jsonl(tmp_path):
    runner = CliRunner()
    res = runner.invoke(
        employers_cli.app,
        [
            "import",
            "--store",
            str(tmp_path / "employers.db"),
            "--data-dir",
            str(tmp_path),
            "--gmail-jsonl",
            str(tmp_path / "does-not-exist.jsonl"),
        ],
    )
    assert res.exit_code == 1
