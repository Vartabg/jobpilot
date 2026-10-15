# Applicant privacy scan

Goal: inspect what an application asks before sharing applicant information.
Show exact field wording, requiredness, decline choices, employer-provided
explanations, and incomplete coverage. Never infer legality, misuse, or safety.

Implementation: public Greenhouse question metadata for up to ten job links;
a user-activated bookmark captures visible field metadata on other ATS pages,
including signed-in steps. It does not read answers, fill fields, click through
steps, solve challenges, or send anything from the employer page. The user
can import the metadata into the local JobPilot report. A dedicated accessible
page supports comparison, required-disclosure filtering and JSON export.

Verification: classifier and URL/network boundary tests, authenticated route
happy/error paths, synthetic form capture without answer leakage, UI keyboard
and reflow checks, and automated accessibility checks. Public data availability
does not establish that later application steps have no additional questions.

Delivery: isolated `codex/applicant-privacy-scan`, finish via agent-task.
Current extension button audit is a separate task; no extension files changed.
