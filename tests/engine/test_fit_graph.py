import pytest

from jobpilot.engine.adapters.fit_graph import GraphFit
from jobpilot.engine.domain import Listing, Posting, Settings, Targets
from jobpilot.engine.domain.fit import ModelError, Tier, Verdict

ME = Settings(
    targets=Targets(titles=("Forward Deployed Engineer",), skills=("Python", "RAG"))
)
REQUIREMENTS = "Requirements:\n- Python backend experience\n- Vector RAG pipelines\n- Kubernetes in production\n"


def posting(title="Forward Deployed Engineer", description=REQUIREMENTS):
    return Posting(
        Listing("Acme", title, url="https://jobs.ashbyhq.com/acme/1"), description
    )


class FakeModel:
    """Replays scripted answers; an exception in the script is raised instead."""

    name = "fake"

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def generate_json(self, prompt, *, schema):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def verdicts(fit):
    return [match.verdict for match in fit.matches]


def test_happy_path():
    model = FakeModel(
        {
            "matches": [
                {
                    "id": "R1",
                    "verdict": "meets",
                    "evidence_ids": ["skill:python"],
                    "note": "listed",
                },
                {
                    "id": "R2",
                    "verdict": "equivalent",
                    "evidence_ids": ["skill:rag"],
                    "note": "RAG work",
                },
                {
                    "id": "R3",
                    "verdict": "gap",
                    "evidence_ids": [],
                    "note": "no Kubernetes",
                },
            ]
        }
    )
    fit = GraphFit(model).assess(posting(), ME)
    assert verdicts(fit) == [Verdict.MEETS, Verdict.EQUIVALENT, Verdict.GAP]
    assert fit.tier is Tier.POSSIBLE  # one gap blocks "strong"
    assert fit.biggest_gap == "Kubernetes in production"
    assert fit.engine == "graph:fake"
    assert len(model.prompts) == 1
    assert "skill:python: Python" in model.prompts[0]


def test_invented_evidence_is_downgraded():
    model = FakeModel(
        {
            "matches": [
                {
                    "id": "R1",
                    "verdict": "meets",
                    "evidence_ids": ["skill:rust"],
                    "note": "made up",
                },
                {
                    "id": "R2",
                    "verdict": "meets",
                    "evidence_ids": ["skill:rag", "skill:rust"],
                    "note": "",
                },
            ]
        }
    )
    fit = GraphFit(model).assess(posting(), ME)
    assert fit.matches[0].verdict is Verdict.UNKNOWN
    assert fit.matches[0].note == "no recorded evidence cited"
    assert fit.matches[1].evidence == ("skill:rag",)
    assert (
        fit.matches[2].note == "not judged"
    )  # missing rows are unknown, never assumed


def test_one_retry_then_success():
    good = {
        "matches": [
            {
                "id": "R1",
                "verdict": "meets",
                "evidence_ids": ["skill:python"],
                "note": "",
            }
        ]
    }
    model = FakeModel({"nonsense": True}, good)
    fit = GraphFit(model).assess(posting(), ME)
    assert len(model.prompts) == 2
    assert fit.matches[0].verdict is Verdict.MEETS
    assert fit.notes == ()


def test_two_bad_answers_fall_back_to_rules():
    model = FakeModel(ModelError("timed out"), {"matches": "nope"})
    fit = GraphFit(model).assess(posting(), ME)
    assert len(model.prompts) == 2
    assert fit.matches[0].verdict is Verdict.MEETS  # rules: "Python" is named
    assert "used the rules check" in fit.notes[0]


def test_off_target_titles_never_call_the_model():
    model = FakeModel()
    fit = GraphFit(model).assess(posting(title="Payroll Manager"), ME)
    assert fit.tier is Tier.OFF_TARGET
    assert model.prompts == []


def test_model_listed_requirements_must_be_quoted_from_the_posting():
    text = "We ship agents with banks. Python is central to everything we do."
    listed = {
        "requirements": [
            {"text": "Python is central to everything we do.", "must": True},
            {"text": "10 years of Rust", "must": True},  # not in the posting
        ]
    }
    judged = {
        "matches": [
            {
                "id": "R1",
                "verdict": "meets",
                "evidence_ids": ["skill:python"],
                "note": "",
            }
        ]
    }
    model = FakeModel(listed, judged)
    fit = GraphFit(model).assess(posting(description=text), ME)
    assert [match.requirement.text for match in fit.matches] == [
        "Python is central to everything we do."
    ]


def test_no_requirements_anywhere_is_possible_without_judging():
    model = FakeModel({"requirements": []})
    fit = GraphFit(model).assess(posting(description="We are a fun team."), ME)
    assert fit.tier is Tier.POSSIBLE
    assert len(model.prompts) == 1  # asked for requirements, never judged


@pytest.mark.parametrize(
    "answer", [{"matches": [{"id": "R9", "verdict": "meets"}]}, {"matches": ["x"]}]
)
def test_out_of_range_or_malformed_rows_are_ignored(answer):
    fit = GraphFit(FakeModel(answer)).assess(posting(), ME)
    assert verdicts(fit) == [Verdict.UNKNOWN] * 3


def test_ids_are_labels_constrained_by_the_schema():
    answer = {
        "matches": [
            {"id": "R2", "verdict": "meets", "evidence_ids": ["skill:rag"], "note": ""}
        ]
    }

    class Recording(FakeModel):
        def generate_json(self, prompt, *, schema):
            self.schema = schema
            return super().generate_json(prompt, schema=schema)

    model = Recording(answer)
    fit = GraphFit(model).assess(posting(), ME)
    item = model.schema["properties"]["matches"]["items"]["properties"]["id"]
    assert item["enum"] == ["R1", "R2", "R3"]
    assert "R1. Python backend experience" in model.prompts[0]
    assert [m.verdict for m in fit.matches] == [
        Verdict.UNKNOWN,
        Verdict.MEETS,
        Verdict.UNKNOWN,
    ]


def test_a_gap_the_record_names_is_downgraded():
    answer = {
        "matches": [
            {"id": "R1", "verdict": "gap", "evidence_ids": [], "note": "no Python"}
        ]
    }
    fit = GraphFit(FakeModel(answer)).assess(posting(), ME)
    first = fit.matches[0]
    assert first.verdict is Verdict.UNKNOWN
    assert first.evidence == ("skill:python",)
    assert "record names it" in first.note
