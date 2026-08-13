"""Candidate evidence must stay optional, versioned, and truth-bounded."""

import json
from pathlib import Path

from jobpilot.core.candidate_evidence import CandidateEvidence
from jobpilot.core.profile_store import UserProfile


def test_missing_account_bank_uses_profile_facts_only(tmp_path: Path):
    profile = UserProfile(skills=["Python"], target_titles=["Implementation Engineer"])

    evidence = CandidateEvidence.load(profile, tmp_path / "missing.json")

    assert evidence.version is None
    assert evidence.skills == ("Python",)
    assert evidence.accounts == ()


def test_loads_versioned_accounts_and_truth_boundaries(tmp_path: Path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({
        "version": 2,
        "accounts": [{
            "id": "field-account",
            "title": "Field systems work",
            "summary": "Troubleshot electronics at customer sites.",
            "skills": ["electronics troubleshooting", "customer training"],
            "details": ["Explained repairs to nontechnical customers."],
            "truth_boundaries": ["Do not claim PLC programming experience."],
            "tags": ["field-primary"],
        }],
    }))

    evidence = CandidateEvidence.load(UserProfile(), path)

    assert evidence.version == 2
    assert evidence.accounts[0].account_id == "field-account"
    assert "PLC programming" in evidence.accounts[0].truth_boundaries[0]
