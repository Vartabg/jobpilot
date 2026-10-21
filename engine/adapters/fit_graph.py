"""A fit checker built as a LangGraph workflow around a language model.

    relevance --(off target)-------------------------------------------> decide
        |
        +--(relevant)--> read_requirements --(found)--> judge --(valid)--> decide
                               |                         |  ^
                               +--(none)--> ask_requirements  +--(bad answer, retry once)
                                                |            |
                                                +--> judge   +--(still bad)--> rules_judge --> decide

Guardrails, all deterministic:
- The model may only quote requirements that appear in the posting text.
- A "meets" or "equivalent" verdict must cite evidence IDs that exist, or it
  becomes "unknown". Target titles are never evidence.
- The tier comes from ``fit.decide``, never from the model.
- A model that can't answer drops the posting to the rules check; it never
  fails the run.
"""

from __future__ import annotations

import re
from typing import Any, Required, TypedDict

from jobpilot.engine.domain import Posting, Settings
from jobpilot.engine.domain.fit import (
    MAX_REQUIREMENTS,
    RELEVANT,
    EvidenceItem,
    Fit,
    Match,
    ModelError,
    Requirement,
    Verdict,
    decide,
    evidence_from,
    extract_requirements,
    match_rules,
    title_relevance,
)
from jobpilot.engine.ports import LanguageModel

MAX_JUDGE_ATTEMPTS = 2

REQUIREMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "must": {"type": "boolean"}},
                "required": ["text", "must"],
            },
        }
    },
    "required": ["requirements"],
}


def judge_schema(count: int) -> dict[str, Any]:
    """The judge answer's shape. Requirement ids are an enum, so they can't be misnumbered."""
    return {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "enum": [f"R{number}" for number in range(1, count + 1)],
                        },
                        "verdict": {
                            "type": "string",
                            "enum": [verdict.value for verdict in Verdict],
                        },
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                        "note": {"type": "string"},
                    },
                    "required": ["id", "verdict", "evidence_ids", "note"],
                },
            }
        },
        "required": ["matches"],
    }


class FitState(TypedDict, total=False):
    posting: Required[Posting]
    settings: Required[Settings]
    notes: Required[tuple[str, ...]]
    attempts: Required[int]
    matches: Required[tuple[Match, ...]]
    evidence: tuple[EvidenceItem, ...]
    relevance: float
    requirements: tuple[Requirement, ...]
    error: str
    fit: Fit


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


class GraphFit:
    """FitChecker that asks a model to judge each requirement, inside fixed guardrails."""

    def __init__(self, model: LanguageModel) -> None:
        self.model = model
        self.name = f"graph:{model.name}"
        self._graph = self._build()

    def assess(self, posting: Posting, settings: Settings) -> Fit:
        state = self._graph.invoke(
            {
                "posting": posting,
                "settings": settings,
                "attempts": 0,
                "notes": (),
                "matches": (),
            }
        )
        return state["fit"]

    # ----- graph ------------------------------------------------------------------

    def _build(self) -> Any:
        from langgraph.graph import (  # heavy; loaded only when used
            END,
            START,
            StateGraph,
        )

        graph = StateGraph(FitState)
        graph.add_node("relevance", self._relevance)
        graph.add_node("read_requirements", self._read_requirements)
        graph.add_node("ask_requirements", self._ask_requirements)
        graph.add_node("judge", self._judge)
        graph.add_node("rules_judge", self._rules_judge)
        graph.add_node("decide", self._decide)
        graph.add_edge(START, "relevance")
        graph.add_conditional_edges(
            "relevance",
            lambda state: (
                "relevant" if state.get("relevance", 0.0) >= RELEVANT else "off_target"
            ),
            {"relevant": "read_requirements", "off_target": "decide"},
        )
        graph.add_conditional_edges(
            "read_requirements",
            lambda state: "found" if state.get("requirements") else "none",
            {"found": "judge", "none": "ask_requirements"},
        )
        graph.add_conditional_edges(
            "ask_requirements",
            lambda state: "found" if state.get("requirements") else "none",
            {"found": "judge", "none": "decide"},
        )
        graph.add_conditional_edges(
            "judge",
            self._after_judge,
            {"valid": "decide", "retry": "judge", "fallback": "rules_judge"},
        )
        graph.add_edge("rules_judge", "decide")
        graph.add_edge("decide", END)
        return graph.compile()

    # ----- nodes ------------------------------------------------------------------

    def _relevance(self, state: FitState) -> FitState:
        settings = state["settings"]
        return {
            "evidence": evidence_from(settings),
            "relevance": title_relevance(
                state["posting"].listing.title, settings.targets.titles
            ),
        }

    def _read_requirements(self, state: FitState) -> FitState:
        return {"requirements": extract_requirements(state["posting"])}

    def _ask_requirements(self, state: FitState) -> FitState:
        posting = state["posting"]
        prompt = (
            "List the candidate requirements stated in this job posting. Quote each one exactly "
            "as written. Mark must=false for anything described as preferred, a plus, or nice to "
            f"have.\n\nPosting title: {posting.listing.title}\n\nPosting text:\n{posting.description}"
        )
        try:
            answer = self.model.generate_json(prompt, schema=REQUIREMENTS_SCHEMA)
        except ModelError as exc:
            return {
                "requirements": (),
                "notes": (*state["notes"], f"model couldn't list requirements: {exc}"),
            }
        source = _squash(posting.description)
        quoted = []
        for item in answer.get("requirements", []) if isinstance(answer, dict) else []:
            text = str(item.get("text", "")).strip() if isinstance(item, dict) else ""
            if text and _squash(text) in source:  # guardrail: no invented requirements
                quoted.append(Requirement(text, bool(item.get("must", True))))
        return {"requirements": tuple(quoted[:MAX_REQUIREMENTS])}

    def _judge(self, state: FitState) -> FitState:
        requirements, evidence = (
            state.get("requirements", ()),
            state.get("evidence", ()),
        )
        attempts = state.get("attempts", 0) + 1
        try:
            answer = self.model.generate_json(
                self._judge_prompt(requirements, evidence),
                schema=judge_schema(len(requirements)),
            )
        except ModelError as exc:
            return {"attempts": attempts, "error": str(exc)}
        rows = answer.get("matches") if isinstance(answer, dict) else None
        if not isinstance(rows, list):
            return {"attempts": attempts, "error": "the answer had no list of matches"}
        return {
            "attempts": attempts,
            "error": "",
            "matches": self._validated(rows, requirements, evidence),
        }

    def _after_judge(self, state: FitState) -> str:
        if not state.get("error"):
            return "valid"
        return "retry" if state.get("attempts", 0) < MAX_JUDGE_ATTEMPTS else "fallback"

    def _rules_judge(self, state: FitState) -> FitState:
        evidence = state.get("evidence", ())
        matches = tuple(
            match_rules(requirement, evidence)
            for requirement in state.get("requirements", ())
        )
        note = f"model answer unusable ({state.get('error', 'unknown error')}); used the rules check"
        return {"matches": matches, "notes": (*state["notes"], note)}

    def _decide(self, state: FitState) -> FitState:
        fit = decide(
            state.get("matches", ()),
            state.get("relevance", 0.0),
            engine=self.name,
            notes=state.get("notes", ()),
        )
        return {"fit": fit}

    # ----- helpers ----------------------------------------------------------------

    @staticmethod
    def _judge_prompt(
        requirements: tuple[Requirement, ...], evidence: tuple[EvidenceItem, ...]
    ) -> str:
        listed_evidence = (
            "\n".join(f"- {item.id}: {item.text}" for item in evidence) or "- (none)"
        )
        listed_requirements = "\n".join(
            f"R{index}. {requirement.text}"
            for index, requirement in enumerate(requirements, start=1)
        )
        return (
            "You are checking a job posting's requirements against a candidate's recorded evidence.\n"
            "Use only the evidence listed. Never assume a skill or experience that isn't listed.\n\n"
            f"Evidence (id: description):\n{listed_evidence}\n\n"
            f"Requirements:\n{listed_requirements}\n\n"
            "For every requirement, give its id (R1, R2, ...) and one verdict:\n"
            '- "meets": the evidence directly covers it. Cite the evidence ids.\n'
            '- "equivalent": the evidence is a credible equivalent. Cite the ids; say why in the note.\n'
            '- "gap": it clearly asks for something the evidence lacks.\n'
            '- "unknown": the evidence doesn\'t say either way.\n'
            "Rules:\n"
            '- A requirement with several parts ("AWS and Git") meets only if every part is covered.\n'
            '- Use "gap" only for a specific skill, tool, domain, or credential the evidence clearly '
            'lacks. Traits and soft skills the evidence doesn\'t mention are "unknown".\n'
            "Keep each note under 15 words."
        )

    @staticmethod
    def _validated(
        rows: list[Any],
        requirements: tuple[Requirement, ...],
        evidence: tuple[EvidenceItem, ...],
    ) -> tuple[Match, ...]:
        known = {item.id for item in evidence}
        by_index: dict[int, Match] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = str(row.get("id", "")).strip().upper()
            index = int(label[1:]) if label[:1] == "R" and label[1:].isdigit() else 0
            if not 1 <= index <= len(requirements) or index in by_index:
                continue
            requirement = requirements[index - 1]
            try:
                verdict = Verdict(str(row.get("verdict", "")).lower())
            except ValueError:
                verdict = Verdict.UNKNOWN
            cited = tuple(
                dict.fromkeys(
                    str(ev) for ev in row.get("evidence_ids") or [] if str(ev) in known
                )
            )
            note = str(row.get("note", ""))[:200]
            if verdict in {Verdict.MEETS, Verdict.EQUIVALENT} and not cited:
                verdict, note = (
                    Verdict.UNKNOWN,
                    "no recorded evidence cited",
                )  # guardrail
            if verdict is Verdict.GAP:
                named = match_rules(requirement, evidence)
                if named.verdict is Verdict.MEETS:  # guardrail: the record names it
                    verdict, cited, note = (
                        Verdict.UNKNOWN,
                        named.evidence,
                        "model said gap, but your record names it",
                    )
            by_index[index] = Match(
                requirement, verdict, cited if verdict is not Verdict.GAP else (), note
            )
        return tuple(
            by_index.get(index, Match(requirement, Verdict.UNKNOWN, (), "not judged"))
            for index, requirement in enumerate(requirements, start=1)
        )
