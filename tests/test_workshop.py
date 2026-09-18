"""Portable workshop packets must never become unearned qualifications."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobpilot.core.workshop_packet import WorkshopPacket
from jobpilot.core.workshop_review import inspect_packet, render_packet

FIXTURE = Path(__file__).parent / "fixtures" / "workshop-packet.json"


def packet():
    return json.loads(FIXTURE.read_text())


def test_confirming_wording_does_not_verify_skills_or_qualifications():
    result = inspect_packet(WorkshopPacket.model_validate(packet()))
    assert result["evidence_status"] == "self_reported"
    assert result["qualification_status"] == "not_assessed"
    assert result["may_submit_automatically"] is False
    assert result["missing"] == []
    assert "not independently verified" in render_packet(result["packet"])


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///private/data",
        "https://user:pass@example.org",
        "https://",
        " https://example.org/\nsecret",
    ],
)
def test_unsafe_source_urls_are_rejected(url):
    raw = packet()
    raw["work"]["source_url"] = url
    with pytest.raises(ValidationError):
        WorkshopPacket.model_validate(raw)


def test_missing_evidence_is_visible_and_not_filled_in():
    raw = packet()
    raw["work"]["validation"] = ""
    raw["direction"]["gap"] = ""
    result = inspect_packet(WorkshopPacket.model_validate(raw))
    assert {item["field"] for item in result["missing"]} == {
        "work.validation",
        "direction.gap",
    }
    assert result["packet"]["work"]["validation"] == ""
    assert result["packet"]["direction"]["gap"] == ""


@pytest.mark.parametrize(
    "change",
    [{"schema_version": "v2"}, {"verified": True}, {"review_status": "verified"}],
)
def test_unknown_contract_or_authority_is_rejected(change):
    raw = packet() | change
    with pytest.raises(ValidationError):
        WorkshopPacket.model_validate(raw)


def test_title_required_but_early_drafts_are_allowed():
    raw = packet()
    raw["work"]["title"] = "  "
    with pytest.raises(ValidationError):
        WorkshopPacket.model_validate(raw)


def test_cli_reads_without_creating_runtime_state(tmp_path):
    script = Path(__file__).parents[1] / "workshop.py"
    result = subprocess.run(
        [sys.executable, str(script), "inspect", str(FIXTURE)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["data"]["evidence_status"] == "self_reported"
    assert list(tmp_path.iterdir()) == []


def test_cli_failure_is_actionable_without_echoing_private_input(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text('{"private": "do not echo this"}')
    script = Path(__file__).parents[1] / "workshop.py"
    result = subprocess.run(
        [sys.executable, str(script), "inspect", str(source)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    error = json.loads(result.stdout)["error"]
    assert error["action"]
    assert "do not echo this" not in result.stdout + result.stderr


def test_cli_rejects_oversized_packet(tmp_path):
    source = tmp_path / "too-large.json"
    source.write_bytes(b" " * 262145)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "workshop.py"),
            "inspect",
            str(source),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "invalid_workshop_packet"
