# 2026-10-15 — Button reliability audit (0.9.1)

Exercise the extension's control groups with synthetic browser fixtures and a
real isolated MV3 installation. Form actions must stay tied to the current job
and tab, and stop across navigation or visibility changes, including while a
dropdown or upload waits. Restrict recognized application scopes so hidden old
forms and unrelated signup controls do not block or receive application data.

Keep user-visible previews consistent with their inputs. Disable edits while
saves run, invalidate changed import previews, preserve explicit selections on
refresh, and allow same-file import retries. The workspace opens through the
Chrome tabs API. Keep the existing permissions and manual submission boundary.
See the [control/page coverage](../extension/BUTTON_AUDIT.md).

# 2026-10-15 — Career memory, application capsules, and a review inbox

Extend the existing Chrome extension with a bundled career workspace tab, using
the same serialized trusted-UI worker broker and live application tracker. Store
the new stories, immutable capsules, and import review history in extension-local
IndexedDB; keep capsule list metadata separate from file bytes. No Python server,
new browser permissions, AI provider, or cloud synchronization is required.

Career wording is reviewed by the candidate and edits invalidate that approval.
Imported accounts need review. Portable JSON/Markdown packets let a person share
selected context with assistants; ordinary chats do not automatically read the
extension. Capsules copy the chosen files and story revisions, preserve exact
answer/posting text, and are sealed only after explicit review. They record a
user's account of a submission, with optional receipts; checksums detect changes
and do not certify delivery to an employer.

XLSX, CSV, TSV, and JSON records enter a pending inbox with source/row provenance.
The applicant resolves conflicts explicitly, and stale reviews/edits are rejected.
No new code fills or submits employer forms. Full workspace backups include
capsule file bytes; profile/tracker backups keep their existing contract. See the
[workspace guide](../extension/WORKSPACE_GUIDE.md) for formats, limits, and checks.

# 2026-09-29 — An extension for the owner's application workflow

The owner requested that JobPilot become a Chrome extension. Start with a
persistent side panel for profile/history editing, a local PDF library, reviewed
user-triggered autofill, and a tracker that distinguishes started from explicitly
submitted applications. Do not rely on a running Python server for these tasks.

Use one worker broker to serialize local storage changes and keep PDF bytes in
IndexedDB. Private profiles and files are passed into the isolated content script
only for the action chosen by the applicant, rather than published as accessible
extension resources. Default packages contain no personal artifacts; a private
package can seed verified data on first use without replacing existing records.

This release supports the seven existing Greenhouse, Lever, and Ashby hosts.
Other pages can be captured and tracked. Visible field reports are observations,
and a selected file alone is not evidence that the portal accepted an upload.
The applicant submits and records that submission; no extension action submits.

The Python engine remains the source of scanning and preparation functionality.
Future scoring and tailoring integration should call that service layer or port
its pure logic, preserving explicit requirement gaps and project evidence. This
extension preview does not revive the parked product backend or claim full
Simplify coverage. See [extension/README.md](../extension/README.md).

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
