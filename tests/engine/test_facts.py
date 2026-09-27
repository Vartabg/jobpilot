import pytest

from jobpilot.engine.domain.facts import (
    annual_pay_max,
    region,
    required_languages,
    required_lines,
    required_years,
    sections,
    travel_percent,
)

PRESALES = """About the role
You will partner with account teams on complex deals.
Qualifications We Value:
- 5+ years in a customer-facing technical role, with at least 3–5 years in pre-sales
- Hands-on experience building AI demos
Bonus points:
- 7+ years of enterprise sales engineering experience
"""


class TestRequiredLines:
    def test_preferred_sections_and_optional_lines_are_skipped(self):
        lines = list(required_lines(PRESALES + "- Spanish fluency is a plus\n"))
        assert any("5+ years" in line for line in lines)
        assert not any("7+ years" in line for line in lines)
        assert not any("Spanish" in line for line in lines)

    def test_short_bullets_are_content_not_headings(self):
        lines = list(required_lines("Requirements:\n- 5+ years of sales experience\n"))
        assert lines == ["5+ years of sales experience"]


def test_all_caps_lines_are_headings_that_end_a_section():
    text = "Nice to have:\n- Fintech experience\nWHY YOU\u2019LL LOVE WORKING HERE\n- Great team\n"
    kinds = [(kind, line) for kind, line in sections(text)]
    assert kinds == [("preferred", "Fintech experience"), ("other", "Great team")]


class TestRequiredYears:
    @pytest.mark.parametrize(
        ("text", "years"),
        [
            (PRESALES, 5),  # the bonus section's 7 doesn't count
            ("Typically requires a minimum of 12 years of related experience", 12),
            ("2–4+ years of experience in solutions engineering", 2),
            ("You'll need 3 or more years working with customers.", 3),
            (
                "You don't need years of consulting; you need to want to be in the room.",
                None,
            ),
            ("Over the next few years, AI will change everything.", None),
            ("We were founded 10 years ago and serve 400 customers.", None),
            ("", None),
        ],
    )
    def test_years(self, text, years):
        assert required_years(text) == years


class TestRequiredLanguages:
    def test_title_languages_count(self):
        assert required_languages(
            "Agent Deployment Engineer - German Speaking", ""
        ) == {"german"}

    def test_fluency_lines_name_every_language(self):
        text = "Requirements:\n- Fluent in French and English\n"
        assert required_languages("Engineer", text) == {"french", "english"}

    def test_optional_languages_do_not_count(self):
        text = "Nice to have:\n- Japanese proficiency\n"
        assert required_languages("Engineer", text) == set()

    def test_unrelated_mentions_do_not_count(self):
        assert (
            required_languages("Engineer", "Our customers span Brazil and Japan.")
            == set()
        )


def test_travel_takes_the_top_of_a_range():
    assert (
        travel_percent(
            "Remote-first with regular client travel (~25–40%, depending on engagement)"
        )
        == 40
    )
    assert travel_percent("Up to 50% travel required.") == 50
    assert travel_percent("No travel.") is None


@pytest.mark.parametrize(
    ("compensation", "top"),
    [
        ("$140K – $200K • Offers Equity", 200_000),
        ("$175,000 - $230,000", 230_000),
        ("CA$150K – CA$200K", None),  # not USD
        ("£110K – £150K", None),
        ("$75/hr", None),  # hourly
        ("", None),
    ],
)
def test_annual_pay_max(compensation, top):
    assert annual_pay_max(compensation) == top


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("United States", "us"),
        ("Remote (US)", "us"),
        ("Austin, TX", "us"),
        ("New York City", "us"),
        ("US or Canada", "us"),
        ("Toronto, Canada (Hybrid)", "non_us"),
        ("London", "non_us"),
        ("Herzliya, Israel", "non_us"),
        ("Latin America", "non_us"),
        ("Remote", "unknown"),
    ],
)
def test_region(location, expected):
    assert region(location) == expected
