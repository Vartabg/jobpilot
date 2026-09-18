# JobPilot product

Goal: A usable local application that saves a person's background, finds real
openings, prepares factual drafts, tracks applications, and reduces repeated
form entry. No teaching or coaching role for the founder.

Delivery: a browser interface served by a standalone loopback-only Python app,
a bundled macOS app, and a product page on ATX BRO. The app has an authenticated
JSON API and export for assistants; no AI account is needed for its core flow.
Public ATS reads are the only required external requests. Personal state lives
in a separate product database, never in Garo's existing job-search files.

Files: `product/models.py`, `product/config.py`, `product/store.py`,
`product/sources.py`, `product/matching.py`, `product/drafts.py`,
`product/api.py`, `product/security.py`, `product/main.py`, `product/static/*`,
`jobpilot_app.py`, `scripts/build_product.py`, `tests/product/*`,
`docs/PRODUCT.md`, `docs/TECH_DEBT.md`, `.github/workflows/product.yml`.

- [x] Profile, public source connectors, source failure reporting, factual matching.
- [x] Draft preparation and editing, persistent tracking, portable exports/API.
- [x] Extension-free bookmarklet for basic details, quick-copy fallback.
- [x] Accessible UI, packaged launcher, ATX BRO product and paid-work copy.
- [x] API/interaction/browser/security checks and real public-board smoke test.
Lifecycle completion is recorded by `agent-task finish` for both isolated branches.

Acceptance: a fresh user completes profile → real search → review → editable
draft → save application status → reopen, without Terminal after app launch.
Autofill is tested only on synthetic forms. It never overwrites existing values,
guesses eligibility, checks consent or submits. All other questions stay manual.
Package and feature limitations are shown honestly; no hiring probabilities,
skill certification or universal ATS support claims.
