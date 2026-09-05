# Security review fixes

Goal: close the four reviewed defects and finish a verified task branch.

Use a shared authentication module for both servers. A private access URL
establishes an HttpOnly, SameSite cookie and redirects to a URL without the
credential. API calls use a custom request header for CSRF protection; no HTML
contains the bearer token. Preserve popup activation while persisting decisions.
Keep corrupt queues in place and retain distinct recovery copies.

- [x] Add failing regression tests for anonymous access, authenticated navigation,
  CSRF, delayed/failed saves, blocked popups, and repeated queue corruption.
- [x] Implement shared authentication in `core/server_auth.py`, settings in
  `core/config.py`, and integration in `core/server.py`, `gigs/server.py`,
  `ui/dashboard.html`, `gigs/swipe.html`, and `cli.py`.
- [x] Retain atomic state writes in `core/atomic_io.py`, `core/profile_store.py`,
  and `core/queue_builder.py`; keep Chrome's existing default origin restriction.
- [x] Run the Python suite, browser interaction/accessibility checks, and review.
Final gate: `agent-task finish` runs verification, secret scans, commit, push,
and synchronization checks. Its returned state is authoritative.

Only the functional portions of the reviewed changes belong to this task;
formatting churn and the original primary worktree are preserved outside it.

Validation: Python and browser regressions reproduce the original failures.
The corrected mobile flow passes a six-second save with popup blocking on.
Axe checks pass for dashboard, install, swipe start/card, and the persistent
email action. Device-only follow-up is recorded in `docs/TECH_DEBT.md`.
