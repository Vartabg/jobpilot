# 2026-11-05 — Human-Guided 3-Step Apply Flow & Situational Controls (v0.15.0)

## Context & Brief
Following the `v0.13.0` top-first layout and `v0.14.2` local bridge integration, the user confirmed they like the top-first layout (`"ok cool i like it, how can we abstract this furrther so its easy to follow along for us humans"`) and selected:
1. **One-click `Prepare application` (`#prepare-application`) + 3-step human checklist** (*Step 1: Prepare application → Step 2: Done for you / Left for you → Step 3: Log submission*), keeping single-purpose `Fill details`, `Inspect fields`, and `Attach selected résumé` accessible right below as secondary actions.
2. **Situational Greenhouse security code (`#security-code-section`):** Visible only on Greenhouse pages (`provider === 'Greenhouse'`) or when the current report includes a security-code field/result or non-empty input; hidden on Ashby, Lever, Workday, and generic sites.
3. **1-Line Job Summary + Collapsible Editor (`#job-details-drawer`) & Tab Cleanup (`Step 3 · Finish & move on`):** Once a job is captured or synced (having `url` and `title` or `company`), `Role`, `Company`, `Job URL`, and `Notes` collapse into a 1-line summary (`Role · Company` with `Edit / notes`), while logging and tab-cleanup actions remain immediately visible:
   - **`Submit & close tab`** (`#submit-close-tab`): Saves the application as `submitted` (auto-capturing/deriving `title`, `company`, and `url` from the active tab if blank) and closes the active job tab via `chrome.tabs.remove(tab.id)`.
   - **`Pass & close tab`** (`#pass-close-tab`): Closes the active job tab immediately when the user decides to move on without applying (and if an existing `started` record exists for that URL, marks it `withdrawn`, or simply closes the tab and refreshes to the next tab).
   - **`Save as started`** (`#save-started`) and **`I submitted this`** (`#save-submitted`): Preserved for logging without closing the tab.
   - **`Close handled job tabs`** (`#close-handled-tabs`) + inline **`Close tab`** in `#duplicate-notice`: Queries open tabs in `currentWindow` (`chrome.tabs.query({ currentWindow: true })`) and closes unpinned tabs whose normalized job URL already has a handled status (`submitted`, `withdrawn`, `rejected`, `interview`, `offer`) in `state.applications`.
4. **Collapsed Bottom Drawer (`#advanced-tools-drawer`):** `Application privacy` (`#scan-privacy`) and `Local assistant` (`#local-connect`, `#local-disconnect`, `#local-status`, `#local-extension-id`) are grouped into a single collapsed `<details id="advanced-tools-drawer">` drawer at the bottom of `Apply` (auto-opening when a saved or fresh privacy scan report is present).

## Planned Changes

### 1. `extension/manifest.json`
- Bump `"version"` from `"0.14.2"` to `"0.15.0"`.

### 2. `extension/sidepanel.html` & `extension/sidepanel.css`
- **Card 1 (`section.card.quick-actions`, `aria-label="Quick actions"`):**
  - Step badge/eyebrow: `Step 1 · Prepare this page` + live `#portal-status` and `#capture-job`.
  - Compact `#profile-summary` and `Résumé to attach` (`#apply-resume`).
  - Primary full-width button: `<button id="prepare-application" class="button primary full prepare-button" type="button">Prepare application</button>`.
  - Compact secondary row (`.secondary-actions`) directly below: `#fill-details` (`Fill details`), `#inspect-form` (`Inspect fields`), `#attach-resume` (`Attach selected résumé`).
  - Situational `<div id="security-code-section" class="security-code-section" hidden>`: `Greenhouse security code` (`#security-code-input`) + `Fill code` (`#fill-security-code`).
- **Card 2 (`#report-card`):**
  - Step heading: `Step 2 · What's done & what's left` (with `#report-heading`, `#report-context`, `#clear-report`).
  - New `<div id="report-checklist" class="human-checklist"></div>` above `#report-summary` showing two plain-English rows:
    - **Done by JobPilot** (`✓ ...`)
    - **Left for you on page** (`→ ...`)
  - Preserves `#report-current-warning`, `#report-summary`, `#report-upload`, `#report-details`, `#report-log-status`, and `#report-problem`.
- **Card 3 (`section.card.current-job`):**
  - Heading: `Step 3 · Finish & move on` + `<button id="close-handled-tabs" class="text-button" type="button">Close handled tabs</button>`.
  - `#duplicate-notice` (including an inline `Close tab` action when the active tab matches an already-saved application).
  - `<details id="job-details-drawer" class="job-details-drawer" open>` with `<summary id="job-summary-toggle"><span id="job-summary-text">Enter or capture role &amp; company</span><span id="job-summary-action" class="summary-pill">Edit / notes</span></summary>` wrapping `#job-form` (`#job-title`, `#job-company`, `#job-url`) and `#job-notes`.
  - Primary tab-cleanup action grid: `#submit-close-tab` (`Submit & close tab`, `.button.primary`) and `#pass-close-tab` (`Pass & close tab`, `.button`).
  - Secondary log-without-closing action grid: `#save-started` (`Save as started`) and `#save-submitted` (`I submitted this`).
- **Card 4 (`<details id="advanced-tools-drawer" class="card tools-drawer">`):**
  - `<summary id="advanced-tools-summary" class="tools-drawer-summary"><span>Privacy scan &amp; local assistant</span><span class="summary-pill">Optional tools</span></summary>`.
  - Inside `<div class="tools-drawer-body">`:
    - `Application privacy` section (`#scan-privacy`, `#privacy-status`, `#privacy-result`).
    - `Local assistant` section (`aria-label="Local assistant connection"`, `#local-status`, `#local-connect`, `#local-disconnect`, `#local-extension-id`).

### 3. `extension/sidepanel.js` & `extension/privacy-panel.js`
- **`prepareApplication()` (`#prepare-application`):**
  - Runs inside `perform(async () => { ... })` so `setBusy(true)` locks all controls once across the combined flow.
  - Rejects early with info notice if `state.dirtyProfile` is true.
  - Syncs/captures the current supported job tab (calling `captureCurrentJob()` when possible to populate `title` and `company` automatically, falling back to tab metadata + URL slug via `deriveJobFallbacks()` if capture fails, and resetting `state.jobDrawerTouched = false` so the captured job collapses cleanly to 1 line).
  - Phase 1: Checkpoints `'details'` with `detailsDiagnosticId` before calling `runCurrentAction('details', state.profile, null, ...)`.
  - Gate before Phase 2: Checks `detailsReport.status !== 'interrupted'`, a valid local PDF résumé is selected (`state.selectedResumeId`), and the page/step supports attachment (`workdayScope?.canAttachResume !== false`).
  - Phase 2: Checkpoints `'resume'` with `resumeDiagnosticId` before calling `runCurrentAction('resume', {}, resume, ...)`, then merges `details` and `resume` via `mergePrepareReport()` (clearing `'Use Attach résumé separately'` from `skipped` when attached and showing `#report-upload` alongside field counts).
- **Tab Cleanup (`submitAndCloseTab()`, `passAndCloseTab()`, `closeHandledTabs()`):**
  - `submitAndCloseTab()` (`#submit-close-tab`): Auto-populates missing `title`/`company`/`url` from `captureCurrentJob()` or `deriveJobFallbacks(activeTab)`, saves the `submitted` record, and only calls `chrome.tabs.remove(activeTab.id)` when the save succeeds and `sameApplicationUrl(state.job.url, activeTab.url)` holds.
  - `passAndCloseTab()` (`#pass-close-tab`): Marks an existing `'started'` record (never `'submitted'`/`'interview'`/`'offer'`) as `'withdrawn'`, closes the active job tab via `chrome.tabs.remove(activeTab.id)`, clears the draft for that tab, and refreshes to the next tab.
  - `closeHandledTabs()` (`#close-handled-tabs`): Queries `chrome.tabs.query({ currentWindow: true })`, filters to unpinned web tabs matching a normalized application URL with status in `['submitted', 'withdrawn', 'rejected', 'interview', 'offer']`, and closes them via `chrome.tabs.remove(tabIds)`.
- **Situational `#security-code-section` visibility (`updateSecurityCodeVisibility()`):**
  - Prioritizes the active tab's provider (`matchesActive && state.job.provider ? state.job.provider : providerFor(activeUrl || state.job.url)`), showing `#security-code-section` only when the current page is `'Greenhouse'`, when `#security-code-input` has text, or when a current-application `state.report` (`!differentApplication`) contains a security-code field/result.
- **1-Line Job Summary (`renderJob()`):**
  - Updates `#job-summary-text` to `${title} · ${company}` (or whichever is present, plus ` · Note added` if `notes` is non-empty).
  - Marks `state.jobDrawerTouched = true` on `#job-summary-toggle` click and `#job-form` / `#job-notes` `input` events so typing never collapses the drawer mid-edit; auto-collapses (`open = false`) when a job is captured/synced and `!state.jobDrawerTouched`.
- **Human Checklist (`renderReport()`):**
  - Populates `#report-checklist` with two concise human-readable items (`✓ Done for you` and `→ Left for you on page`) based on `groups.filled`, `resumeResults`, `groups.required`, `groups.failed`, and `incomplete`, and shows `#report-upload` whenever `action === 'resume' || resumeResults.length > 0`.
- **`extension/privacy-panel.js`:**
  - When `report` is non-null in `render()`, sets `document.getElementById('advanced-tools-drawer')?.setAttribute('open', '')` so restored or completed privacy scans are immediately visible.

### 4. Tests & Verification
- Preserve the exact `function setBusy(busy) {` to `\nfunction updateActionButtons()` slice boundary in `sidepanel.js` for `extension/tests/privacy-panel-state.test.mjs`, and set `$('#prepare-application').disabled = state.busy` in `updateActionButtons()`.
- Update `extension/tests/sidepanel.test.mjs`:
  - Add `remove: async tabIds => { globalThis.__extensionCalls.push({ tabsRemoved: [tabIds].flat() }); }` to `chrome.tabs`.
  - Open `#advanced-tools-summary` in the tests that click `#scan-privacy` or `#local-connect` or measure `#scan-privacy`.
  - Add comprehensive tests for:
    1. One-click `#prepare-application` capturing the job, filling details, attaching the selected PDF, collapsing the job summary to 1 line, and rendering the 2-line human checklist (`#report-checklist`).
    2. Situational `#security-code-section` visible on Greenhouse and hidden on Ashby/Workday/Lever unless a security-code field is reported.
    3. `#job-details-drawer` collapsing once a job is captured and expanding when `#job-summary-toggle` is clicked.
    4. Tab cleanup (`#submit-close-tab`, `#pass-close-tab`, and `#close-handled-tabs`) recording outcomes and closing only the intended browser tabs via `chrome.tabs.remove`.
- Run `node --test extension/tests/*.test.mjs`, `python3 -m unittest tests/test_extension_package.py`, and `python3 -m unittest tests/test_local_bridge.py`.
- Visually inspect rendered `320px` and `400px` screenshots across interactive states.
