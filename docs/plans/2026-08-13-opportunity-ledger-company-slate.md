# Opportunity Ledger + Company Slate — Implementation Plan

**Goal:** Evolve JobPilot into a full-JD, evidence-linked, legitimacy-gated company slate that preserves application history and learns only from real outcomes.
**Approach:** Add a versioned opportunity ledger and deterministic decision engine beside the legacy queue, then route queue creation through them as a strangler migration. Preserve current CLI/UI contracts while adding explicit decision, evidence, legitimacy, freshness, and outcome fields; retire title-only “personality” claims and keep automation on the documented human-submit boundary.
**Files affected:** `core/opportunity_ledger.py`, `core/opportunity_models.py`, `core/role_identity.py`, `core/legitimacy.py`, `core/candidate_evidence.py`, `core/requirement_matcher.py`, `core/role_decision.py`, `core/portal_scanner.py`, `core/queue_builder.py`, `core/profile_store.py`, `core/application_evidence.py`, `core/application_tracker.py`, `core/resume_tailor.py`, `core/server.py`, `cli.py`, `ui/dashboard.html`, `gigs/swipe.html`, `tests/`, `README.md`, `docs/APPLICATION_FLOW.md`.

---

### Task 1: Establish the accessibility and truth-safety floor
**Files:** Modify `ui/dashboard.html`, `gigs/swipe.html`, `core/profile_store.py`, `core/resume_tailor.py`, `docs/APPLICATION_FLOW.md`; create/modify tests under `tests/`.

1. Bring the existing accessibility-baseline changes onto the task branch.
2. Add failing tests proving `skills`, `target_titles`, and domain experience survive profile load/save and unknown experience never renders as `0 years`.
3. Add failing tests proving missing skills and internal fit/gap notes never enter an external resume.
4. Implement the smallest corrections and run the focused tests.

- [x] Task 1 complete

### Task 2: Capture direct-source job evidence
**Files:** Create `core/opportunity_models.py`, `core/role_identity.py`; modify `core/portal_scanner.py`; create `tests/test_portal_provenance.py`, `tests/test_role_identity.py`.

1. Define normalized source observations with provider tenant/job ID, fetched time, listing state, full description, location, posting metadata, compensation, and canonical URL.
2. Make Greenhouse request `content=true`; preserve Lever descriptions/workplace metadata and Ashby descriptions/listed state/compensation.
3. Add provider-aware role identity and URL normalization that preserves identity-bearing query parameters such as Indeed `jk` while removing tracking parameters.
4. Record target-level source success, zero-result, and failure states.

- [x] Task 2 complete

### Task 3: Add deterministic legitimacy assessment
**Files:** Create `core/legitimacy.py`; create `tests/test_legitimacy.py`.

1. Add evidence grades `A`–`F` and states `recommend`, `review`, `hold`, `block` without presenting them as calibrated probabilities.
2. Require current direct ATS proof for `recommend`; keep unresolved aggregators/recruiters at `review`, expired verification at `hold`, and closed/expired/conflicting/scam evidence at `block`.
3. Cover 404/closed state, past expiry, aggregator-only leads, source outage, fee/check/crypto/equipment-purchase scams, and unknown dates.

- [x] Task 3 complete

### Task 4: Build the append-only opportunity ledger
**Files:** Create `core/opportunity_ledger.py`; modify `core/application_evidence.py`, `core/application_tracker.py`; create `tests/test_opportunity_ledger.py`, modify `tests/test_application_tracker.py`.

1. Add idempotent SQLite tables for opportunities, source runs, observations, assessments, and append-only funnel events.
2. Import tracker, Gmail-cache, and application-packet evidence with provenance; retain conflicting source events for review.
3. Support discovered, verified, applied, outreach, human reply, screen, interview, offer, rejected, withdrawn, and no-response events.
4. Remove company-wide rejection propagation: only a matching provider role or explicit company policy may suppress another opportunity.

- [x] Task 4 complete

### Task 5: Replace personality keywords with evidence-linked decisions
**Files:** Create `core/candidate_evidence.py`, `core/requirement_matcher.py`, `core/role_decision.py`; modify `core/profile_store.py`; create `tests/test_role_decision.py`, `tests/test_candidate_evidence.py`.

1. Load optional versioned true accounts and profile skills/target titles without requiring personal data in a fresh clone.
2. Classify bounded role families before scoring so marketing, product, design, and data-science titles cannot be rescued by `field` or `forward deployed` substrings.
3. Split full-JD text into mandatory, preferred, responsibility, work-context, and logistics evidence.
4. Match every contribution to a candidate account or explicit profile fact; distinguish direct, adjacent, unknown, and contradicted evidence.
5. Emit separate qualification lower bound, work-context match, opportunity, logistics, evidence coverage, biggest gap, matched accounts, and `apply_now | stretch | investigate | skip` decision.
6. Return `unscorable` for missing/insufficient JDs; never award neutral points for unknown evidence.

- [x] Task 5 complete

### Task 6: Make the company slate the default queue
**Files:** Modify `core/queue_builder.py`, `cli.py`, `core/server.py`; create/modify queue, CLI, and server tests.

1. Enrich `QueueJob` compatibly with decision, axis, evidence, legitimacy, verification, posting, and gap fields.
2. Route refreshed jobs through legitimacy + full-JD decision assessment and persist observations/assessments in the ledger.
3. Automatically keep one active best role per company; retain alternatives as skipped with a structured reason.
4. Make cached verification expiry fail closed: stale roles become `hold/investigate` and cannot emit as `apply_now`.
5. Reconcile application evidence on cached CLI output and return plain-English API errors with next actions.

- [x] Task 6 complete

### Task 7: Present an accessible evidence card
**Files:** Modify `ui/dashboard.html`, `core/server.py`; create/modify dashboard and accessibility tests.

1. Rename “personality” to work-context evidence and remove double counting from sorting.
2. Show decision, legitimacy grade/state, verification age, qualification lower bound, evidence coverage, matched life account, and biggest gap separately.
3. Disable application actions for `review`, `hold`, `block`, or `investigate` roles while retaining a clear investigation path.
4. Preserve keyboard, screen-reader, reduced-motion, forced-colors, zoom, and coarse-pointer behavior.

- [x] Task 7 complete

### Task 8: Add outcome reporting and public documentation
**Files:** Modify `core/analytics.py`, `cli.py`, `README.md`; create/modify analytics and CLI tests.

1. Add time-scoped funnel metrics for human reply, screen, interview, offer, rejection, withdrawal, and censored no-response.
2. Report lane/channel sample sizes and refuse automatic weight updates below 20 decided outcomes.
3. Document the company-slate workflow, evidence semantics, and human-submit safety boundary.

- [x] Task 8 complete

### Task 9: Review, verify, and finish
**Files:** All task-owned files.

1. Run Pass 0 hard constraints, secret checks, schema safety, and automation/human-submit checks.
2. Run focused tests, full pytest, targeted Ruff on task files, and accessibility contracts.
3. Review the diff for personal data, unsupported claims, files over 200 lines, and unrelated changes.
4. Finish with `agent-task finish`, requiring commit, history secret audit, push, clean worktree, and `COMPLETE` state.

- [x] Task 9 complete
