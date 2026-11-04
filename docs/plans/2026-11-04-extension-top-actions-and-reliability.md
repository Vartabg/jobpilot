# 2026-11-04 — JobPilot Chrome Extension Top-First Layout & Reliability Plan (v0.13.0)

## 1. Context & Problem Statement

The owner reported three concrete problems with the JobPilot Chrome extension:
1. **Excessive wordiness and information overload:** Every section in the side panel carries subtitles, multi-sentence `.fine-print` paragraphs explaining what JobPilot does *not* do, and expanded scan/report blocks that obscure actionable controls.
2. **Inverted visual hierarchy:** In both `main` (`0.8.2`) and the installed unpacked build (`0.12.2` from `codex/career-workspace`), the primary form controls (`Fill details`, `Attach selected résumé`, `Inspect fields`) and portal availability status are buried below a tall header, a full-width workspace link bar, the `Current job` text form (`Role`, `Company`, `Job URL`), and—in `0.12.2`—the `Application privacy` card (which auto-expands any previously saved scan on startup above the action buttons).
3. **Intermittent failures ("hasn't been working lately"):**
   - **Stale draft lock (`expectedUrl` mismatch):** `sidepanel.js` persists `state.job` in `chrome.storage.local` (`jobpilot_ui_v1`) and passes `expectedUrl: state.job.url` to `runCurrentAction()`. When the user switches to a new job tab and clicks `Fill details`, `Attach selected résumé`, `Inspect fields`, or `Fill code` without clicking `Capture tab` first, `runCurrentAction()` rejects with `"The current tab is a different job from your saved draft. Capture this tab before filling or attaching a résumé."`
   - **False-positive loading lock (`tab.status === 'loading'`):** In `0.12.2`, `activePage()` in `actions.js` rejects whenever `tab.status === 'loading'`, even when `tab.pendingUrl` is absent or identical to `url.href`. Slow third-party tracking pixels or background subresource loads after a combobox interaction cause `activePage()` or `unchangedPage()` to fail on an already-interactive form.
   - **Preflight `hasForm` gaps for Ashby and Greenhouse security-code prompts:** In `actions.js`, `hasForm` checks `el.closest('form, [role="form"], #application-form')` or `/first.?name|last.?name|email|resume|application/i` on `name`/`id`/`aria-label`. Ashby fields wrapped in `.ashby-application-form-field-entry` without a `<form>` ancestor or Greenhouse verification code inputs (`#security-input-0..7`, `security_code`) can be rejected before `content.js` runs.
   - **Diverged extension codebases:** `main` is at `0.8.2` (missing Workday support, voluntary disclosure preferences, toolbar `onClicked` `activeTab` fix, diagnostics history, privacy scan, and bundled workspace from `0.12.2`), while the installed unpacked build (`0.12.2` at `~/job-search/JobPilot-Autofill-2026-09-29 2`) is missing yesterday's Greenhouse 8-character security-code helper (`c6c00b9` on `codex/greenhouse-security-code`).

## 2. Goals & Non-Goals

### Goals
1. **Unify the extension on `main` as `v0.13.0`:** Bring the self-contained `0.12.2` extension modules (`Workday`, voluntary disclosures, toolbar `onClicked` grant, diagnostics history, privacy scanner, career workspace) together with the `c6c00b9` Greenhouse security-code helper into `extension/`, `scripts/package_extension.py`, and `tests/test_extension_package.py`.
2. **Top-first `Apply` tab hierarchy:**
   - **Compact single-row header:** Brand (`JobPilot`) on the left, `Workspace ↗` (`#open-workspace`) pill on the right.
   - **Card 1 (Very Top of `Apply`, `aria-label="Quick actions"`): Availability & Primary Actions:**
     - **Live Availability Banner (`#portal-status`) + `Capture tab` (`#capture-job`)** at the very top of `#panel-apply`. Automatically reflects the active tab via `chrome.tabs.query({ active: true, currentWindow: true })` on startup and on `chrome.tabs.onActivated` / `chrome.tabs.onUpdated` without injecting scripts (`chrome.scripting.executeScript` is never called automatically).
     - **Compact profile & résumé selector (`#profile-summary`, `#apply-resume`)** immediately below availability.
     - **Primary action buttons (`#fill-details`, `#inspect-form`, `#attach-resume`)** and the compact **Greenhouse security code** row (`#security-code-input`, `#fill-security-code`) immediately visible above the fold.
   - **Card 2: Compact Form Results (`#report-card`):** Immediately below the primary action buttons when an action runs. Actionable groups (`Needs review`, `Still required`, `Failed`) open by default; `Filled` and `Skipped` remain collapsed (`<details>`) so 20+ skipped rows do not flood the panel.
   - **Card 3: Compact Job Details & Tracker:** Combines `#job-form` (`#job-title`, `#job-company`, `#job-url`), `#duplicate-notice`, `#job-notes`, and tracker actions (`#save-started`, `#save-submitted`) into one concise card without redundant subtitles or disclaimer paragraphs.
   - **Card 4 (Bottom of `Apply`): Application Privacy (`#scan-privacy`, `#privacy-result`):** Positioned at the bottom of `#panel-apply` so a restored or active privacy scan never pushes the primary autofill controls down.
3. **Eliminate wordiness across `Apply`, `Profile`, `Applications`, and `History`:**
   - Remove redundant card subtitles and repetitive `.fine-print` disclaimer paragraphs while keeping all required `<label>`, `aria-labelledby`, `role="status"`, and `aria-live="polite"` hooks intact (and omitting dangling `aria-describedby` references).
4. **Fix the intermittent reliability failures:**
   - **Auto-sync active supported tab on action click in `sidepanel.js`:** Before `runAction(action)` invokes `runCurrentAction`, query `chrome.tabs.query({ active: true, currentWindow: true })`. If the active tab is a supported application page (`isSupportedPage(activeTab.url)`) and `state.job.url` is empty or has a different canonical job identity (`!sameApplicationUrl(state.job.url, activeTab.url)`), update `state.job` to the active tab (preserving typed `company` / `notes` if `state.job.url` was previously empty, or resetting them when switching from a different job URL) and re-render before running the action. Also update `state.activeTab` inside `#capture-job` so `renderPortalStatus()` always reflects the newly captured tab.
   - **Fix `activePage()` loading check in `actions.js`:** Reject when `tab.pendingUrl && tab.pendingUrl !== url.href` (actual pending navigation to another URL) rather than rejecting whenever `tab.status === 'loading'` on an already-addressed page.
   - **Broaden `hasForm` preflight detection in `actions.js`:** Include `.ashby-application-form-field-entry` in the container selector and `security[-_]?input|security[-_]?code|verification[-_]?code` in the control attribute pattern.
   - **Support `security-code` action across `actions.js`, `content.js`, `diagnostics.js`, and `sidepanel.js`:** Integrate `c6c00b9`'s segmented (`#security-input-0..7`) and single-input Greenhouse verification code autofill with `0.12.2`'s interruption guards (`guard()`) and diagnostic run checkpointing (`REASONS`, `TYPES`, `sanitizeRun`, `checkpointRun`).

### Non-Goals
- No changes to manifest permissions (`['storage', 'sidePanel', 'scripting', 'activeTab']`) or host permissions (7 Greenhouse, Lever, and Ashby hosts).
- No auto-submission of employer forms, no background script injection on tab switch, and no overwriting of existing applicant answers.
- No changes to Python backend services outside `scripts/package_extension.py` and `tests/test_extension_package.py`.

## 3. Detailed File-Level Design (Updated Post-EGM Review)

### `extension/manifest.json`
- Bump `"version"` to `"0.13.0"`.
- Keep `"permissions": ["storage", "sidePanel", "scripting", "activeTab"]` and the 7 ATS `"host_permissions"` unchanged.

### `extension/actions.js`
- Extend `runCurrentAction(action, profile, resume = null, {expectedUrl = '', securityCode = '', onProgress} = {})` to accept `"security-code"`:
  - Validate `if (action === "security-code" && !String(securityCode || "").trim()) throw new Error("Enter the 8-character security code from your Greenhouse email first.");`
  - Pass `...(action === "security-code" && !ambiguous && candidates.length ? {securityCode: String(securityCode || '')} : {})` to `__jobpilotRun` while keeping `profile: {}` and `resume: null` for `"security-code"`.
  - In Workday candidate and target filtering (`lines 182-184` and `240-241`), explicitly check `action === 'resume' ? frame.result.workdayScope?.canAttachResume : false` so `'security-code'` is never routed to a Workday résumé step.
- In `activePage()`:
  - Replace `if (tab.status === 'loading' || tab.pendingUrl && tab.pendingUrl !== url.href)` with `if (tab.pendingUrl && tab.pendingUrl !== url.href)` so slow subresources do not block interactive pages while real pending navigations are still rejected.
- In preflight `hasForm`:
  - Check `el.closest('form, [role="form"], #application-form, .ashby-application-form-field-entry') || /first.?name|last.?name|email|resume|application|security[-_]?input|security[-_]?code|verification[-_]?code/i.test([el.name, el.id, el.getAttribute('aria-label')].join(' '))`.

### `extension/content.js`
- Merge `c6c00b9`'s Greenhouse security-code support into `0.12.2`'s `content.js`:
  - `isSecurityCodeInput`, `segmentedSecurityInput`, `normalizeSecurityCode`, `securityCodeTarget()` (filtering candidates with `!unrelatedControl(el)`), and `fillSecurityCode(rawCode, guard)` (threading `guard` through `setText(..., guard)` and checking `guard()` after `await pause(50)`).
  - In `inventory()`:
    - Allow security-code controls even if rendered outside `scopes`: `(!scopes.length || scopes.some(scope => scope.contains(el)) || isSecurityCodeInput(el) || segmentedSecurityInput(el)) && !unrelatedControl(el)`.
    - Group segmented security inputs (`input[id^='security-input-'], input[name^='security-input-'], input[data-testid^='security-input-']`) into `{el, label: 'Security code', type: 'security-code', required: group.some(required), answered: group.every(answered)}`.
  - In `applicationScopes()`:
    - Only fall back to matching a `<form>` / `[role="form"]` by security-code inputs when no primary application form is already matched, so a separate verification `<form>` alongside `#application-form` never causes a false `applicationScopes().length > 1` abort.
  - In `__jobpilotRun({action='inspect', profile={}, resume=null, securityCode='', ...})`:
    - Accept `'security-code'` in valid actions and initialize `securityCode: {status: 'not-requested', message: ''}` on `report`.
    - During `'details'`, skip `type === 'security-code' || isSecurityCodeInput(el)` with reason `'Use Fill code in the side panel'`.
    - When `action === 'security-code'`, run `fillSecurityCode(securityCode, guard)`. If `report.securityCode.status === 'filled'`, push to `report.filled`; if `'no-target'`, push to `report.skipped`; if `'interrupted'`, leave `report.status = 'interrupted'`; otherwise push to `report.failed`. Mark `report.status = 'partial'` if `report.status === 'complete'` and `['invalid-code', 'failed', 'unsupported'].includes(report.securityCode.status)`.

### `extension/diagnostics.js`
- Add `'security-code'` to `TYPES` (`line 66`) and to `sanitizeRun`'s `action` allowlist (`choices(input.action, ['inspect', 'details', 'resume', 'security-code'], 'inspect')` at `line 142`).
- Register security-code reasons in `REASONS`:
  - `'Use Fill code in the side panel'` -> `security_code_manual: 'Use Fill code in the side panel'` (and include `'security_code_manual'` in `manual` at `line 139` so skipping a security code field during `Fill details` does not classify the run as an open problem).
  - `'Security code autofill is only supported on Greenhouse application pages.'` -> `security_code_unsupported`
  - `'Enter the 8-character security code from your Greenhouse verification email.'` -> `security_code_invalid`
  - `'No visible security code field was found on this page. Submit the form first if Greenhouse has not shown the code prompt yet.'` -> `security_code_missing`
  - `'The form did not retain every security code character. Check the code boxes on the page.'` / `'The form did not retain the security code. Enter it directly on the page.'` -> `security_code_unconfirmed`
  - Normalize dynamic length-mismatch messages (`/^This security code prompt has \d+ character boxes/`) to `security_code_invalid` in `code()`.

### `extension/sidepanel.html` & `extension/sidepanel.css`
- Retain `<a class="privacy-skip" href="#main">Skip to workspace</a>` and `<main id="main" tabindex="-1">`.
- **Compact Header:** Single horizontal bar with `JobPilot` brand on the left and `<a id="open-workspace" class="workspace-pill" href="workspace.html" target="_blank" rel="noopener">Workspace ↗</a>` on the right.
- **`#panel-apply` Card Order:**
  1. **Card 1 (`<section class="card quick-actions" aria-label="Quick actions">`):**
     - Top availability bar: `<div id="portal-status" class="portal-status" role="status">...</div>` + `<button id="capture-job" class="button small" type="button">Capture tab</button>`
     - Profile & Résumé bar: `<div id="profile-summary" class="profile-summary">...</div>` + `<label>Résumé to attach<select id="apply-resume">...</select></label>`
     - Primary action grid:
       - `<div class="action-grid"><button id="fill-details" class="button primary" type="button">Fill details</button><button id="inspect-form" class="button" type="button">Inspect fields</button></div>`
       - `<button id="attach-resume" class="button full" type="button">Attach selected résumé</button>`
     - Compact Greenhouse security-code row (without dangling `aria-describedby`):
       - `<div class="security-code-section"><label for="security-code-input">Greenhouse security code</label><div class="security-code-row"><input id="security-code-input" name="security_code" type="text" autocomplete="one-time-code" autocorrect="off" autocapitalize="off" spellcheck="false" maxlength="120" placeholder="8-character code from email"><button id="fill-security-code" class="button small" type="button">Fill code</button></div></div>`
  2. **Card 2 (`<section id="report-card" class="card report-card" hidden>`):**
     - Immediately below Card 1 so action results appear right under the clicked button.
  3. **Card 3 (`<section class="card current-job">`):**
     - Compact heading `<h2>Current job & tracker</h2>`
     - `#duplicate-notice`
     - `<form id="job-form">` (`Role` `#job-title`, `Company` `#job-company`, `Job URL` `#job-url`)
     - `<label>Notes<textarea id="job-notes" rows="2" ...></textarea></label>`
     - `<div class="action-grid"><button id="save-started" type="button" class="button">Save as started</button><button id="save-submitted" type="button" class="button primary">I submitted this</button></div>`
  4. **Card 4 (`<section class="card" aria-labelledby="privacy-card-heading">` — `Application privacy`):**
     - Compact heading `<h2>Application privacy</h2>` + `#scan-privacy` + `#privacy-status` + `#privacy-result`.

### `extension/sidepanel.js`
- Preserve `function setBusy(busy) {` through `\nfunction updateActionButtons()` slice boundary used by `extension/tests/privacy-panel-state.test.mjs`.
- Add `refreshActiveTab()` using read-only `chrome.tabs?.query({ active: true, currentWindow: true })`:
  - Updates `state.activeTab = tab ? { id: tab.id, url: tab.url || '', title: tab.title || '' } : null` and calls `renderPortalStatus()`.
  - Hook `chrome.tabs?.onActivated?.addListener(() => void refreshActiveTab())` and `chrome.tabs?.onUpdated?.addListener(() => void refreshActiveTab())`.
  - Also update `state.activeTab` inside `#capture-job`'s handler (`state.activeTab = { id: state.activeTab?.id, url: job.url || '', title: job.title || '' }`) before calling `renderJob()`.
- Update `runAction(action)`:
  - Validate `state.dirtyProfile && action === 'details'` and `action === 'security-code' && !$('#security-code-input').value.trim()` before creating `state.report` or calling `checkpointRun()`, focusing `#security-code-input` after `perform()` releases `setBusy(false)`.
  - Query `chrome.tabs?.query({ active: true, currentWindow: true })` to refresh `state.activeTab`. If `activeTab?.url` is a supported page (`isSupportedPage(activeTab.url)`) and `state.job.url` is empty or belongs to a different job (`!sameApplicationUrl(state.job.url, activeTab.url)`), auto-sync `state.job` (preserving typed `company` / `notes` if `state.job.url` was previously empty) and call `renderJob()`.
  - Include `'security-code': 'Fill security code'` in `renderHistory()`'s action label dictionary and check `securityStatus` in `runAction()`'s completion notice.

## 4. Verification Plan
1. Run all Node/Playwright extension test suites (`node --test extension/tests/*.test.mjs`) and Python packaging tests (`python3 -m unittest tests/test_extension_package.py`).
2. Add explicit regression tests for:
   - Top-first layout order in `sidepanel.test.mjs` (verifying `#portal-status`, `#fill-details`, `#inspect-form`, `#attach-resume`, and `#fill-security-code` appear above `#job-form` and `#scan-privacy` in visual Y-coordinates).
   - Auto-syncing `state.job` when switching to a new supported job tab and clicking `Fill details` without clicking `Capture tab` first (no `"different job from your saved draft"` error).
   - Greenhouse security-code validation and segmented/single-box autofill (`actions.test.mjs`, `content.test.mjs`, `sidepanel.test.mjs`).
3. Render and visually inspect screenshots of the side panel at `320px` and `400px` widths across interactive states.
4. Back up and update the user's unpacked installation at `/Users/vartny/job-search/JobPilot-Autofill-2026-09-29 2` (preserving private local files byte-for-byte), then complete the git workflow via `agent-task finish`.
