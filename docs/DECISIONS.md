# 2026-11-05 — Human-guided 3-step Apply flow and situational controls (0.15.0)

## Concept & User Feedback Record
- **Who it is for & purpose:** The applicant moving through live job applications who wants the side panel to read like a simple human checklist (*1. Prepare application → 2. See what's done & what's left for you → 3. Log submission*) instead of a flat toolbox of separate mechanics and developer plumbing.
- **Concrete action-to-result relationship:**
  - Clicking one primary button (**`Prepare application`**, `#prepare-application`) syncs/captures the active job tab, fills saved profile details, and attaches the selected PDF résumé in one coordinated sequence, then displays a plain-English 2-line human checklist (`#report-checklist`): **✓ Done for you** and **→ Left for you on the page**.
  - Situational and secondary controls stay out of the way until needed: the Greenhouse 8-character security code box (`#security-code-section`) hides on non-Greenhouse pages unless a security-code field is detected; `Job details & tracker` collapses its text inputs into a 1-line `Role · Company` summary bar (`#job-details-drawer`) once a job is captured; and `Application privacy` + `Local assistant` live in a collapsed `Privacy scan & local assistant` drawer (`#advanced-tools-drawer`) at the bottom of `Apply`.
  - Tab cleanup is built directly into Step 3 (`Finish & move on`): clicking **`Submit & close tab`** (`#submit-close-tab`) records the submission (auto-syncing role/company from the active tab if needed) and closes the browser tab; clicking **`Pass & close tab`** (`#pass-close-tab`) closes a tab the user decided to move on from and advances to the next tab; and **`Close handled tabs`** (`#close-handled-tabs`) closes open job tabs in the current window that already match submitted/withdrawn/rejected tracker records.
- **Accepted by user:**
  - Top-first layout from `0.13.0` (`ok cool i like it`).
  - Approach 1: One-click **`Prepare application`** + 3-step human checklist (*Done for you → Left for you → Log submission*), while keeping single-action `Fill details`, `Inspect fields`, and `Attach selected résumé` accessible for granular use.
  - Hiding the Greenhouse security code section unless on a Greenhouse page or when a security code field is detected.
  - Collapsing `Job details & tracker` inputs into a 1-line `Role · Company` summary once captured until `Edit / notes` is clicked, keeping `Save as started` and `I submitted this` immediately clickable.
  - Moving `Local assistant` (CLI bridge) and `Application privacy` into a collapsed bottom drawer.
  - Direct tab-closing actions for tabs that have been applied to (`Submit & close tab`, `Close handled tabs`) or chosen to move on from (`Pass & close tab`).
- **Rejected by user:**
  - Multi-screen wizard mode that hides steps behind Next/Back navigation.
  - Keeping all situational cards (`Greenhouse security code`, full `Job details` inputs, `Local assistant`, `Application privacy`) permanently expanded on every page.
- **Undecided / deferred:**
  - Automated multi-page navigation across ATS steps (remains intentionally manual).

# 2026-11-04 — Top-first side panel layout, Greenhouse security code, and active-tab reliability (0.13.0)

## Concept & User Feedback Record
- **Who it is for & purpose:** The owner applying across Greenhouse, Lever, Ashby, Workday, and custom career sites who needs immediate visibility into what JobPilot can do on the active tab and one-click access to primary form actions (`Fill details`, `Attach selected résumé`, `Inspect fields`, and `Fill code`) without wading through defensive disclaimers or scrolling past secondary cards.
- **Concrete action-to-result relationship:**
  - Opening the side panel or switching tabs immediately updates the top availability banner (`#portal-status`) using read-only tab metadata (`chrome.tabs.query`), showing the detected portal and available actions (`Greenhouse · Ready to fill details, attach PDF, inspect, or fill security code`, `Workday · Ready to inspect, fill details, or attach PDF on this step`, `Lever / Ashby · Ready to fill details, attach PDF, or inspect fields`, `Manual tracking only`, or `Click the JobPilot toolbar icon on the job tab to connect`).
  - Clicking `Fill details`, `Attach selected résumé`, `Inspect fields`, or `Fill code` at the top of the panel automatically syncs the draft target when the active tab is a different supported job page from the previously stored `chrome.storage.local` draft, eliminating false `"different job from your saved draft"` rejections while preserving mid-action navigation guards.
- **Accepted by user:**
  - Top-first `Apply` tab hierarchy: live page availability status and primary action controls (`Fill details`, `Attach selected résumé`, `Inspect fields`, résumé selector, and compact Greenhouse 8-character security-code helper) at the very top of the panel.
  - Drastic reduction of wordy subtitles, repetitive `.fine-print` disclaimers, and visual noise across cards and form reports (keeping `Filled` and `Skipped` groups collapsed by default while opening actionable `Still required`, `Failed`, and `Needs review` items).
  - Unifying the diverged extension builds (`main` v0.8.2, `codex/greenhouse-security-code`, and the installed v0.12.2 Workday/disclosure/privacy/workspace build) and fixing intermittent autofill/capture failures (`expectedUrl` stale-draft lock, `tab.status === 'loading'` subresource false-positive abort, and Ashby/security-code preflight form detection).
- **Rejected by user:**
  - Burying primary autofill/inspect/resume buttons beneath the `Current job` text form and `Application privacy` scan results.
  - Verbose explanatory copy and repetitive defensive disclaimers on every card.
- **Undecided / deferred:**
  - Whether to add new ATS providers beyond Greenhouse, Lever, Ashby, and Workday in future releases.

# 2026-10-15 — Workday candidate applications (0.10.0)

Support verified public Workday job routes on tenant.wdN.myworkdayjobs.com and
wdN.myworkdaysite.com/recruiting/tenant/site, including locale prefixes and
application suffixes. Use the existing toolbar-triggered activeTab grant;
do not request persistent access to every Workday tenant or employee portals.

Scope actions to the current recognized application step, exclude account
gates, and stop on step replacement even if the URL remains unchanged.
Fill only verified field mappings and existing history rows; attach a selected
local PDF separately. Sign-in, account creation, questions, disclosures,
attestations, navigation and submission stay manual. Canonical job identity
keeps tenant/site/job boundaries and reconciles older generic Workday tracker
records without rewriting sealed capsules. Test both candidate URL families
through real DOM fixtures and isolated MV3 scripting.

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
# 2026-11-04: Explicit local application controller

The applicant requested a local alternative built on the installed Chrome
extension and continued applications with employer-question evidence, without
further TinyFish spending. Version 0.14 adds an opt-in native-messaging connection
to an owner-only Unix socket and a standard-library Python controller. It uses
explicit job/tab/document/snapshot bindings and individual supplied answers.
Demographic preferences are excluded from local bulk filling. Legal/personal
decisions require an explicit per-question answer; the controller does not infer
consent, create accounts or solve challenges.

This adds explicit final submission to the local controller, superseding the
earlier manual-only final-click decision for this authorized workflow. Preparation
checks the current form, accepted PDF evidence, required answers and one final
button; a short-lived nonce permits one click. The worker records attempts before
dispatch and prevents automatic retries across reload. Success still requires a
separate exact employer receipt before updating the canonical tracker. The
existing manual UI's final submission behavior is preserved.

Exact fixed question labels, choices, constraints and observation provenance can
be exported without entered applicant values. This is evidence of the current
visible form state, not a complete survey. Workday and embedded flows remain
manual pending a separate multistep contract. Design and review evidence are in
`docs/plans/2026-11-04-local-application-bridge*.md`.
