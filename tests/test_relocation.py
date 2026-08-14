from jobpilot.core.relocation import (
    RelocationState,
    assess_relocation,
    relocation_sort_rank,
)


def test_explicit_company_paid_relocation_is_preserved_with_evidence() -> None:
    result = assess_relocation(
        description=(
            "Responsibilities\nBuild customer systems.\n"
            "Benefits\nRelocation assistance is provided for this position."
        ),
        location="Seattle, WA",
        home_city="Austin",
    )

    assert result.state is RelocationState.OFFERED
    assert result.supported
    assert result.move_required is True
    assert result.destination == "Seattle, WA"
    assert result.evidence == ("Relocation assistance is provided for this position.",)


def test_conditional_support_never_becomes_confirmed_support() -> None:
    result = assess_relocation(
        description="Relocation assistance may be available based on business need.",
        location="Denver, CO",
        home_city="Austin",
    )

    assert result.state is RelocationState.CONDITIONAL
    assert result.supported
    assert result.confidence == 90


def test_required_relocation_without_support_is_not_an_offer() -> None:
    result = assess_relocation(
        description="Candidates must be willing to relocate to the assigned office.",
        location="New York, NY",
        home_city="Austin",
    )

    assert result.state is RelocationState.REQUIRED_UNSUPPORTED
    assert not result.supported
    assert result.move_required is True


def test_explicit_no_support_wins_over_generic_relocation_language() -> None:
    result = assess_relocation(
        description=(
            "You must relocate before starting. No relocation assistance is provided."
        ),
        location="Boston, MA",
        home_city="Austin",
    )

    assert result.state is RelocationState.NOT_OFFERED
    assert not result.supported


def test_remote_and_home_metro_are_not_mislabeled_as_relocation_offers() -> None:
    remote = assess_relocation(
        description="Work with customers across the country.",
        location="Remote — United States",
        workplace_type="remote",
        home_city="Austin",
    )
    local = assess_relocation(
        description="Work on site with customers.",
        location="Austin, TX",
        workplace_type="onsite",
        home_city="Austin",
    )

    assert remote.state is RelocationState.NOT_NEEDED
    assert local.state is RelocationState.NOT_NEEDED
    assert not remote.supported
    assert not local.supported


def test_company_headquarters_mentions_do_not_invent_destination_or_locality() -> None:
    result = assess_relocation(
        description=(
            "We are headquartered in Austin. Relocation support is available for this role."
        ),
        location="Berlin, Germany",
        home_city="Austin",
    )

    assert result.state is RelocationState.OFFERED
    assert result.destination == "Berlin, Germany"
    assert result.move_required is True


def test_relocation_sort_is_explicit_and_invalid_states_rank_last() -> None:
    assert relocation_sort_rank("offered") > relocation_sort_rank("conditional")
    assert relocation_sort_rank("conditional") > relocation_sort_rank("not_needed")
    assert relocation_sort_rank("not-a-state") == 0
