# Application Freshness Gate — Implementation Plan

**Goal:** Ensure every `jobpilot jobs` recommendation is new, policy-eligible, and backed by reconciled application evidence before it is shown.
**Approach:** Add one read-only evidence index that merges the canonical tracker with Employment packet folders and optional Gmail-export evidence, then route queue output through it. Keep claim-lock at staging, preserve human submission, and expose plain-English suppression/health diagnostics.
**Files affected:** `core/application_evidence.py`, `core/queue_builder.py`, `core/policy_config.py`, `core/doctor.py`, `core/cdp_bridge.py`, `cli.py`, `scripts/launch_chrome.sh`, `docs/policy.example.json`, targeted tests, and gitignored personal policy/evidence data.

---

### Task 1: Canonical evidence index and DB-path guard
**Files:** Create `core/application_evidence.py`; modify `core/doctor.py`; create `tests/test_application_evidence.py`; modify `tests/test_doctor.py`

1. Write failing tests for canonical DB selection, repo-root DB warnings, Employment packet detection, normalized company/title matching, and fail-closed uncertain evidence.
2. Run the focused tests and confirm failure.
3. Implement the evidence index and doctor diagnostics without deleting user files.
4. Run focused tests and confirm pass.

- [x] Task 1 complete

### Task 2: Fresh queue and personal policy gates
**Files:** Modify `core/queue_builder.py`, `core/policy_config.py`, `data/policy.json`, `docs/policy.example.json`; modify `tests/test_queue_reconcile.py`

1. Write failing tests showing an Employment packet, refused company, and mobile no-car role are suppressed.
2. Run the focused tests and confirm failure.
3. Apply evidence and transport/refusal gates before queue output while preserving discovery metadata and claim-lock ownership.
4. Run focused tests and confirm pass.

- [x] Task 2 complete

### Task 3: CLI freshness semantics and claim-state clarity
**Files:** Modify `cli.py`; create `tests/test_cli_freshness.py`

1. Write failing tests that `jobpilot jobs` reconciles before output and reports a plain-English failure when evidence cannot be read.
2. Run the focused tests and confirm failure.
3. Route `jobs` through the shared freshness gate and include non-authoritative claim-state metadata without moving claim-lock into `queue.json`.
4. Run focused tests and confirm pass.

- [x] Task 3 complete

### Task 4: Verification, review, and release
**Files:** All task files above

1. Run focused and broader tests.
2. Run the three-pass review, secret audit, live `doctor`, `jobs`, Ollama, Chrome, and phone endpoint checks. Add the Chrome 150 launch compatibility flag found during live verification.
3. Update the JobPilot diary.
4. Stage only task hunks, commit, and push the current non-default branch.

- [x] Task 4 complete
