# 2026-09-25 — One core, the owner's search first

Rebuild JobPilot as one core instead of three apps sharing a folder. Serve the
owner's own search first; the public app continues later on the same core, and
`product/` is parked meanwhile. Keep a Python engine with plain web screens; a
TypeScript UI can follow on the same service layer.

Use ports and adapters: clients call one service layer, the domain core is pure
Python, and sources, the fit agent, storage, and rendering are plug-ins. Reuse
the design, not the merge, of the unmerged opportunity-ledger branch. Phase 0
makes the test suite hermetic and fixes two data-loss bugs before any
restructuring. Plan: [plans/2026-09-25-one-core.md](plans/2026-09-25-one-core.md).

# 2026-09-07 — A product for unconventional career changes

Ship a standalone local app with ordinary controls, persistent personal state,
authenticated API and explicit exports. Core tasks require no AI account. Use
literal resume evidence for application starters, and preserve edits. The app
owns repeatable state and operations an assistant can reuse across sessions;
it does not claim a model cannot perform those tasks independently.

Build separately from the legacy personal automation so the distributable never
imports private profiles, credentials, browser sessions or job-search records.
Use public board APIs and extension-free, user-activated contact filling. Keep
eligibility, consent and submission manual. The public source download uses an
allowlist rather than exposing the existing private Git repository.

JobPilot and shared resources are free. ATX BRO's paid offer is scoped software
work with written terms, without coaching, spiritual pressure or hiring promises.

Verification: product API/provider/storage/packaging tests; existing legacy suite;
real public-board reads; browser workflow with synthetic application forms;
packaged binary launch, persistence and quit; website build, regression suite and
accessible product/download flow. See the PR and product guide for exact evidence
and the remaining manual/distribution checks.
