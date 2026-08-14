# Agent Service Facade — Implementation Plan

**Goal:** Give every authorized AI agent one versioned, model-neutral JobPilot contract while preserving the human review and submit boundary.
**Approach:** Add a small service facade over existing queue, decision, dashboard, and tracker capabilities; expose it through Python, versioned HTTP routes, and a JSON-only CLI subcommand. Existing dashboard and CLI routes will delegate to the facade without changing their legacy response shapes. The facade will return explicit warnings, side-effect metadata, and required human actions instead of model-specific assumptions.
**Files affected:** `core/agent_contract.py`, `core/jobpilot_service.py`, `core/assessment_service.py`, `core/outcome_service.py`, `core/target_review.py`, `core/tracker_service.py`, `core/application_tracker.py`, `core/agent_api.py`, `core/server.py`, `cli_agent.py`, `cli.py`, `pyproject.toml`, `tests/test_agent_service.py`, `tests/test_target_review.py`, `tests/test_application_tracker.py`, `tests/test_agent_api.py`, `tests/test_cli_agent.py`, `tests/test_wheel_install.py`, `README.md`, `docs/AGENT_API.md`.

---

### Task 1: Define the stable agent contract
**Files:** Create `core/agent_contract.py`; create `tests/test_agent_service.py`

1. Add failing tests for a versioned envelope, capability discovery, model independence, and the human-submit boundary.
2. Implement immutable operation metadata and deterministic envelope serialization.
3. Run `python3.12 -m pytest -q tests/test_agent_service.py`.

- [x] Task 1 complete

### Task 2: Centralize opportunity orchestration
**Files:** Create `core/jobpilot_service.py`, `core/assessment_service.py`, `core/outcome_service.py`, `core/target_review.py`, `core/tracker_service.py`; modify `core/application_tracker.py`, `tests/test_agent_service.py`, `tests/test_application_tracker.py`; create `tests/test_target_review.py`

1. Add failing tests for cached and refreshed opportunity lists, apply-ready filtering, relocation evidence, assessment, preflight, dashboard metrics, and outcome recording.
2. Implement `JobPilotService` using existing queue, decision, provenance, dashboard, and tracker functions; move model-specific target-review naming behind a neutral compatibility reader.
3. Ensure reads use explicit read-only SQLite connections and do not create or migrate state; label refresh and outcome recording as local-state mutations.
4. Run the focused service suite and Ruff.

- [x] Task 2 complete

### Task 3: Add thin transport adapters
**Files:** Create `core/agent_api.py`, `cli_agent.py`; modify `core/server.py`, `cli.py`; create `tests/test_agent_api.py`, `tests/test_cli_agent.py`

1. Add failing HTTP tests for capability discovery, opportunity listing, assessment validation, and fail-closed errors.
2. Add failing CLI tests for `jobpilot agent capabilities`, `opportunities`, `assess`, and invalid input.
3. Route versioned HTTP and JSON-only CLI commands through `JobPilotService`.
4. Keep existing `/api/queue`, `/api/dashboard`, and `jobpilot queue --json` shapes compatible while delegating their data acquisition to the service.
5. Run transport, server, and CLI compatibility tests.

- [x] Task 3 complete

### Task 4: Document and release-gate the abstraction
**Files:** Modify `README.md`, `pyproject.toml`, `tests/test_wheel_install.py`; create `docs/AGENT_API.md`

1. Document supported transports, envelope schema, operations, side effects, warnings, and human responsibilities.
2. State that agents may discover, assess, sort, draft, and record, but cannot fill or submit live ATS forms.
3. Run the full test suite, packaging smoke test, compile check, Ruff, and diff check.
4. Finish through `agent-task finish`, merge into the active branch, push, reinstall, and verify the installed contract.

- [x] Task 4 complete
