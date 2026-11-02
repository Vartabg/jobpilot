import pytest

from jobpilot.engine.domain import (
    CompanyRules,
    LevelRules,
    Listing,
    LocationRules,
    Outcome,
    Posting,
    Profile,
    Remote,
    Rules,
    Screening,
    Settings,
    Workplace,
    rules_fingerprint,
    screen,
)

MINE = Settings(
    profile=Profile(languages=("English", "Armenian", "Spanish")),
    rules=Rules(
        location=LocationRules(home=("Austin, TX",), remote=Remote.US, relocate=False),
        level=LevelRules(
            exclude_titles=("senior", "sr", "staff", "lead", "head of", "intern"),
            max_years_required=4,
        ),
        companies=CompanyRules(exclude=("Initech",)),
    ),
)


def posting(
    title="Solutions Engineer",
    company="Acme",
    description="",
    workplace=Workplace.UNKNOWN,
    locations=(),
    **extra,
):
    return Posting(
        Listing(company, title, url="https://jobs.ashbyhq.com/acme/1"),
        description,
        workplace,
        locations,
        **extra,
    )


def outcomes(result: Screening) -> dict[str, Outcome]:
    return {check.rule: check.outcome for check in result.checks}


class TestTodaysHandCheck:
    """The 2026-09-25 hand-check, as regression cases."""

    def test_onsite_bay_area_role_asking_five_years_fails_twice(self):
        result = screen(
            posting(
                title="Agent Deployment Engineer",
                workplace=Workplace.ONSITE,
                locations=("San Francisco",),
                description="Requirements:\n- 5+ years of experience in a technical, customer-facing role",
            ),
            MINE,
        )
        assert not result.passed
        assert {check.rule for check in result.failures} == {"location", "years"}
        assert "On-site in San Francisco; your home is Austin, TX" in [
            c.detail for c in result.failures
        ]

    def test_german_speaking_london_role_fails_on_language_and_location(self):
        result = screen(
            posting(
                title="Agent Deployment Engineer - German Speaking",
                workplace=Workplace.ONSITE,
                locations=("London",),
            ),
            MINE,
        )
        assert {check.rule for check in result.failures} == {"location", "language"}

    def test_spanish_speaking_role_passes_the_language_rule(self):
        result = screen(
            posting(
                title="Engineer - Spanish Speaking",
                workplace=Workplace.REMOTE,
                locations=("United States",),
            ),
            MINE,
        )
        assert result.passed

    def test_remote_us_builder_role_passes(self):
        result = screen(
            posting(
                title="Forward Deployed Engineer",
                workplace=Workplace.REMOTE,
                locations=("United States",),
                description="Remote-first with regular client travel (~25–40%). You don't need years of consulting.",
                compensation="$140K – $200K",
            ),
            MINE,
        )
        assert result.passed
        assert outcomes(result)["location"] is Outcome.PASS

    def test_twelve_year_role_fails_years(self):
        result = screen(
            posting(
                workplace=Workplace.REMOTE,
                locations=("Remote - US",),
                description="Typically requires a minimum of 12 years of related experience",
            ),
            MINE,
        )
        assert [check.detail for check in result.failures] == [
            "Asks for 12+ years; your limit is 4"
        ]

    def test_two_to_four_year_role_passes(self):
        result = screen(
            posting(
                workplace=Workplace.REMOTE,
                locations=("Remote, US",),
                description="2–4+ years of experience in solutions engineering",
            ),
            MINE,
        )
        assert result.passed


class TestLocation:
    def test_home_city_passes_even_when_on_site(self):
        result = screen(
            posting(workplace=Workplace.HYBRID, locations=("Austin, Texas",)), MINE
        )
        assert outcomes(result)["location"] is Outcome.PASS

    def test_unlabeled_remote_in_the_location_text_counts_as_remote(self):
        result = screen(posting(locations=("Remote - United States",)), MINE)
        assert outcomes(result)["location"] is Outcome.PASS

    def test_remote_without_a_country_is_unsure_not_failed(self):
        result = screen(
            posting(workplace=Workplace.REMOTE, locations=("Remote",)), MINE
        )
        assert outcomes(result)["location"] is Outcome.UNKNOWN
        assert result.passed

    def test_remote_outside_the_us_fails(self):
        result = screen(
            posting(workplace=Workplace.REMOTE, locations=("Toronto, Canada",)), MINE
        )
        assert outcomes(result)["location"] is Outcome.FAIL

    @pytest.mark.parametrize(
        "workplace", [Workplace.UNKNOWN, Workplace.HYBRID, Workplace.ONSITE]
    )
    def test_a_country_without_a_city_is_unsure_not_failed(self, workplace):
        result = screen(
            posting(workplace=workplace, locations=("United States",)), MINE
        )
        assert outcomes(result)["location"] is Outcome.UNKNOWN

    def test_relocating_opens_other_cities(self):
        movable = Settings(
            rules=Rules(location=LocationRules(home=("Austin, TX",), relocate=True))
        )
        assert screen(
            posting(workplace=Workplace.ONSITE, locations=("Denver, CO",)), movable
        ).passed

    def test_no_remote_setting_fails_remote_roles(self):
        homebody = Settings(
            rules=Rules(
                location=LocationRules(home=("Austin, TX",), remote=Remote.NONE)
            )
        )
        assert not screen(
            posting(workplace=Workplace.REMOTE, locations=("United States",)), homebody
        ).passed


class TestOtherRules:
    @pytest.mark.parametrize(
        ("title", "passes"),
        [
            ("Senior Solutions Engineer", False),
            ("Sr. Customer Engineer", False),
            ("Staff Engineer", False),
            ("Team Lead, Implementation", False),
            ("Head of Solutions", False),
            ("Forward Deployed Engineering Intern", False),
            ("Leadership Development Engineer", True),  # "lead" only as a whole word
            ("Internal Tools Engineer", True),  # "intern" only as a whole word
            ("Solutions Engineer", True),
        ],
    )
    def test_title_words(self, title, passes):
        result = screen(
            posting(
                title=title, workplace=Workplace.REMOTE, locations=("United States",)
            ),
            MINE,
        )
        assert outcomes(result)["level"] is (Outcome.PASS if passes else Outcome.FAIL)

    def test_excluded_company_ignores_suffixes(self):
        result = screen(
            posting(
                company="Initech, Inc.", workplace=Workplace.REMOTE, locations=("US",)
            ),
            MINE,
        )
        assert [check.rule for check in result.failures] == ["company"]

    def test_pay_floor_and_travel_limit(self):
        strict = Settings(
            rules=Rules(
                location=LocationRules(remote=Remote.ANYWHERE),
                pay_floor=150_000,
                max_travel_percent=20,
            )
        )
        result = screen(
            posting(
                workplace=Workplace.REMOTE,
                compensation="$90K – $120K",
                description="Up to 50% travel.",
            ),
            strict,
        )
        assert {check.rule for check in result.failures} == {"pay", "travel"}

    def test_defaults_screen_nothing_out(self):
        assert screen(
            posting(title="Senior Staff Lead", description="15+ years of experience"),
            Settings(),
        ).checks


class TestRecords:
    def test_round_trip(self):
        result = screen(
            posting(workplace=Workplace.ONSITE, locations=("London",)), MINE
        )
        assert Screening.from_dict(result.to_dict()) == result
        assert result.to_dict()["passed"] is False

    def test_fingerprint_follows_the_rules(self):
        assert rules_fingerprint(MINE) == rules_fingerprint(MINE)
        assert rules_fingerprint(MINE) != rules_fingerprint(Settings())
