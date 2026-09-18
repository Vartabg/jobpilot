"""P0#2: Himalayas company recovery from listing URL."""

from jobpilot.gigs.core.models import Gig
from jobpilot.gigs.core.pipeline import Row, rescore_new_rows
from jobpilot.gigs.core.scrapers.himalayas import (
    _location_hint,
    company_from_url,
)


def test_company_from_url_title_cases_slug() -> None:
    url = (
        "https://himalayas.app/companies/medable/jobs/"
        "forward-deployed-engineer-agentic-ai-for-clinical-development"
    )
    assert company_from_url(url) == "Medable"


def test_company_from_url_title_cases_multi_part_slug() -> None:
    url = "https://himalayas.app/companies/rimini-street-inc/jobs/forward-deployed-engineer-agentic-ai-304319220"
    assert company_from_url(url) == "Rimini Street Inc"


def test_company_from_url_empty_when_no_companies_segment() -> None:
    assert company_from_url("https://himalayas.app/jobs/foo") == ""
    assert company_from_url("") == ""


def test_location_hint_picks_parenthetical_market() -> None:
    assert _location_hint("Founding AI Engineer (India)", "", "https://x/y") == "India"
    assert (
        _location_hint(
            "Frontend Engineer – Remote (UK)",
            "whatever",
            "https://himalayas.app/companies/bjak/jobs/frontend-engineer-remote-uk",
        )
        == "UK"
    )


def test_rescore_backfills_placeholder_company_from_url() -> None:
    """Pipeline rows that stored company='himalayas' get a real name on rescore
    when the apply URL carries /companies/{slug}/."""
    rows = [
        Row(
            status="new",
            score=100,
            company="himalayas",
            role="Forward Deployed Engineer - Agentic AI",
            apply=(
                "https://himalayas.app/companies/medable/jobs/"
                "forward-deployed-engineer-agentic-ai-for-clinical-development"
            ),
            gig_id="himalayas-forward-deployed-engineer-agentic-ai-for-clinical-development",
        ),
    ]
    # No collected gigs — recovery must work from the row's apply URL alone.
    changed = rescore_new_rows(rows, collected=[])
    assert changed >= 1
    assert rows[0].company == "Medable"


def test_rescore_prefers_collected_gig_company() -> None:
    rows = [
        Row(
            status="new",
            score=80,
            company="himalayas",
            role="AI Engineer",
            apply="https://himalayas.app/companies/old-slug/jobs/ai-engineer",
            gig_id="himalayas-ai-engineer",
        ),
    ]
    collected = [
        Gig(
            id="himalayas-ai-engineer",
            source="himalayas",
            title="AI Engineer",
            company="Dialpad",
            url="https://himalayas.app/companies/dialpad/jobs/ai-engineer",
            description="agent claude mcp",
        ),
    ]
    rescore_new_rows(rows, collected)
    assert rows[0].company == "Dialpad"
