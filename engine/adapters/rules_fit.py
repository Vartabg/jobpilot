"""The rules-only fit checker: deterministic, fast, and fully on this computer."""

from __future__ import annotations

from jobpilot.engine.domain import Posting, Settings
from jobpilot.engine.domain.fit import Fit, assess_with_rules


class RulesFit:
    name = "rules"

    def assess(self, posting: Posting, settings: Settings) -> Fit:
        return assess_with_rules(posting, settings)
