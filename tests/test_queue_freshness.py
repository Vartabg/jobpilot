"""Queue freshness gates combine evidence and personal policy."""

from pathlib import Path

import jobpilot.core.queue_builder as qb
from jobpilot.core.application_evidence import ApplicationEvidenceIndex
from jobpilot.core.application_tracker import ApplicationTracker
from jobpilot.core.policy_config import policy_from_dict, set_policy
from jobpilot.core.queue_builder import QueueJob


def _job(**overrides) -> QueueJob:
    values = {
        "id": "job-1",
        "company": "Acme",
        "title": "Solutions Engineer",
        "url": "https://jobs.example.test/acme/1",
        "location": "Remote, United States",
        "portal": "ashby",
        "track": "tech",
        "fit_score": 80,
        "keywords": ["solutions"],
        "status": "queued",
    }
    values.update(overrides)
    return QueueJob(**values)


def test_reconcile_suppresses_employment_packet(monkeypatch, tmp_path: Path):
    tracker = ApplicationTracker(data_dir=tmp_path / "data")
    employment = tmp_path / "Employment"
    packet = employment / "Switch-DCOFacilitiesTech1-2026-07-11"
    packet.mkdir(parents=True)
    (packet / "notes.md").write_text("# Switch — DCO Facilities Technician I\n")
    evidence = ApplicationEvidenceIndex.build(
        tracker=tracker, employment_dir=employment
    )

    monkeypatch.setattr(qb, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(qb, "get_application_tracker", lambda: tracker)
    monkeypatch.setattr(
        qb, "get_application_evidence_index", lambda _tracker=None: evidence
    )
    set_policy(policy_from_dict({}))
    qb.save_queue([_job(company="Switch", title="DCO Facilities Technician I")])

    changed, total = qb.reconcile_queue_with_tracker()

    assert (changed, total) == (1, 1)
    assert qb.load_queue()[0].status == "started"
    tracker.close()
    set_policy(None)


def test_policy_blocks_refused_company_and_mobile_transit_role():
    set_policy(
        policy_from_dict(
            {
                "scoring": {"refused_companies": {"honeywell": "user exclusion"}},
                "queue": {
                    "transport_gate": {
                        "enabled": True,
                        "mode": "transit",
                        "mobile_title_keywords": ["field service", "route technician"],
                        "fixed_site_title_keywords": [
                            "data center",
                            "critical environments",
                        ],
                    },
                },
            }
        )
    )

    assert qb.policy_block_reason("Honeywell", "Solutions Engineer")
    assert qb.policy_block_reason("Acme", "Field Service Technician")
    assert qb.policy_block_reason("Acme", "Data Center Technician") is None
    set_policy(None)


def test_policy_allowlist_removes_keyword_noise_outside_role_lane():
    set_policy(
        policy_from_dict(
            {
                "queue": {
                    "title_allow_keywords": [
                        "solutions",
                        "implementation",
                        "forward deployed",
                    ],
                },
            }
        )
    )

    assert qb.policy_block_reason("Acme", "Founding Product Designer")
    assert qb.policy_block_reason("Acme", "Lead QA Engineer")
    assert qb.policy_block_reason("Acme", "Solutions Engineer") is None
    set_policy(None)
