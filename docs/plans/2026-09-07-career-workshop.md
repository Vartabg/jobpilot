# Career evidence workshop

**Goal:** Let a person or their AI inspect and reuse the browser workshop's portable evidence packet without private runtime data or network access.
**Approach:** Add a strict Pydantic packet contract, deterministic review and Markdown handoff, and a standalone JSON-capable CLI. Packet text remains untrusted, self-reported evidence. No skills certification or live application action.
**Files affected:** `core/workshop_packet.py`, `core/workshop_review.py`, `workshop.py`, `tests/test_workshop.py`, `tests/fixtures/workshop-packet.json`, `docs/WORKSHOP.md`.

- [x] Write contract and CLI tests including invalid packets, unsafe URLs, missing evidence, and honest review states.
- [x] Implement strict packet parsing, review diagnostics, Markdown handoff and local CLI.
- [x] Confirm a browser export works through the Python reader and that no user state is created.
- [x] Run verification and inspect the diff; prepare the isolated change for task-lifecycle gates.

## Verification

703 Python tests passed; 3 existing skips and 2 dependency deprecation warnings.
The 14 workshop checks and Ruff lint/format pass. A real JSON download from
the production ATX BRO browser preview was accepted by this reader, retained
`wording_confirmed`, and remained self-reported and not assessed. The CLI is
read-only and the tests confirm it creates no runtime state. Release completion
is determined by `agent-task finish`, then the branch is proposed for review.
