# CDP runtime hardening

**Mode:** QUEST — a browser profile is durable user state, so process ownership
and cleanup must be proven rather than inferred.

## Done means

- JobPilot reuses a CDP listener or launches one detached system-Chrome process.
- Default disconnect detaches Playwright without closing Chrome.
- A live profile never receives a second Chrome process; stale locks are
  removed only after the process check is clear.
- Internal Chrome pages are never selected for script injection.
- No remote-origin wildcard is added to the CDP command.
- Every touched Python file remains below 200 lines.

## Implementation

1. Keep environment-derived Chrome paths in `core/config.py`.
2. Isolate process/profile ownership in `core/chrome_runtime.py`.
3. Isolate safe tab choice in `core/tab_selection.py`.
4. Keep `core/cdp_bridge.py` focused on Playwright attach/detach behavior.
5. Cover selection, ownership, lock, and disconnect behavior with unit tests.
6. Launch a temporary-profile Chrome on a unique port; prove attach, detach
   survival, and exact-process cleanup.

## Verification

- Focused browser/doctor tests: 13 passed; full suite: 685 passed, 3 skipped.
- Ruff lint/format: passed.
- Real Chrome on port 9333: attached to LinkedIn jobs, remained reachable after
  `disconnect()`, and the listener stopped after terminating its exact
  temporary-profile PID.
- Canonical JobPilot and AI_Workspace checkouts were not mutated.
