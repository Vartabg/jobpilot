# Consolidated EGM Design & Concept Review: `2026-11-04-extension-top-actions-and-reliability.md`

- **Reviewed Plan:** `docs/plans/2026-11-04-extension-top-actions-and-reliability.md`
- **Reviewer Context:** Fresh-context independent `research` subagent (`4c29e953-2d2a-49c6-b54c-7568d73613b5`)
- **Status:** All `P1` (3), `P2` (2), and `P3` (1) findings resolved in the updated plan before implementation.

## Summary of Findings & Resolutions

1. **`[P1]` `refreshActiveTab()` leaves `state.activeTab` stale when `chrome.tabs.query` is updated without tab events, breaking `renderPortalStatus()` after `#capture-job`**
   - **Finding:** Tests in `sidepanel.test.mjs:199-215` override `chrome.tabs.query` without dispatching `chrome.tabs.onActivated`/`onUpdated` and click `#capture-job`. If `#capture-job` does not update `state.activeTab`, `sameApplicationUrl(state.job.url, state.activeTab.url)` is `false` and `renderPortalStatus()` falls back to the initial tab URL.
   - **Resolution:** Updated `#capture-job` and `runAction()` in the plan and implementation to sync `state.activeTab` whenever `captureCurrentJob()` or `chrome.tabs.query` runs.

2. **`[P1]` `diagnostics.js` (`sanitizeRun`) and `sidepanel.js` (`renderHistory`) coerce `'security-code'` runs to `'inspect'`, strip `type: 'security-code'`, and misclassify security-code reasons**
   - **Finding:** `diagnostics.js` allowlists `['inspect', 'details', 'resume']` for `action`, omits `'security-code'` from `TYPES`, and lacks reason mappings for `'Use Fill code in the side panel'` and `fillSecurityCode` status messages (including the dynamic character-box count message). `renderHistory()` in `sidepanel.js:577` also lacked `'security-code'` in its action title dictionary.
   - **Resolution:** Added `'security-code'` to `sanitizeRun`'s `action` allowlist, `TYPES`, and `renderHistory()`; mapped all security-code reasons in `REASONS` (normalizing dynamic box-count messages to `security_code_invalid`); and added `'security_code_manual'` to `manual` at `diagnostics.js:139`.

3. **`[P1]` `applicationScopes()` and `inventory()` in `0.12.2` `content.js` can either exclude portal security-code inputs or trigger a false multi-form abort (`applicationScopes().length > 1`)**
   - **Finding:** Unconditionally adding security-code forms to `applicationScopes()` can cause `applicationScopes().length > 1` when Greenhouse renders a separate verification `<form>` alongside `#application-form`, whereas keeping them out of `applicationScopes()` filters them out of `inventory()` when rendered outside `#application-form`.
   - **Resolution:** Updated `applicationScopes()` to fall back to security-code forms only when no primary application form is matched, updated `inventory()` to allow `isSecurityCodeInput(el) || segmentedSecurityInput(el)` outside `scopes` (while filtering `!unrelatedControl(el)`), and filtered `securityCodeTarget()` with `!unrelatedControl(el)`.

4. **`[P2]` `fillSecurityCode()` and `actions.js` Workday candidate filtering need `0.12.2`'s interruption and step guards**
   - **Finding:** `fillSecurityCode()` in `c6c00b9` did not thread `0.12.2`'s `guard` callback into `setText` or check `guard()` after `await pause(50)`. Also, `actions.js:184, 241` used an implicit else-branch for `canAttachResume` that would match `'security-code'` on Workday résumé steps.
   - **Resolution:** Threaded `guard` into `fillSecurityCode(rawCode, guard)` and made `action === 'resume' ? frame.result.workdayScope?.canAttachResume : false` explicit in `actions.js`.

5. **`[P2]` Empty security-code validation focus under `setBusy(true)` and preserving typed draft fields during `runAction()` auto-sync**
   - **Finding:** Calling `$('#security-code-input').focus()` inside `perform()` while `$('main').inert = true` is a no-op, and auto-syncing `state.job` from an empty `state.job.url` should preserve any `company` or `notes` already typed into the form.
   - **Resolution:** Validate empty `#security-code-input` before `state.report`/`checkpointRun()` and focus `#security-code-input` after `perform()` releases `setBusy(false)`; preserve `state.job.company` and `state.job.notes` when `state.job.url` was previously empty.

6. **`[P3]` Dangling `aria-describedby` and landmark labeling in `sidepanel.html`**
   - **Finding:** Removing `#security-code-help` requires omitting `aria-describedby="security-code-help"` from `#security-code-input`; Card 1 (`section.card.quick-actions`) needs `aria-label="Quick actions"`; `.privacy-skip` and `<main id="main" tabindex="-1">` from `0.12.2` must be preserved.
   - **Resolution:** Applied all three HTML accessibility refinements in the plan and implementation.
