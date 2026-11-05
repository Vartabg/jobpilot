# 2026-11-05 — Independent EGM Review: Human-Guided 3-Step Apply Flow & Tab Cleanup (v0.15.0)

Reviewer: Independent `research` subagent (`a58ac4d5-b289-49f3-a858-13042a3db841`)

## Findings & Resolutions

1. **`[P1]` Combined `Prepare application` (`details` + `resume`) report, checkpoint, and Workday details-only step coordination**
   - **Issue:** `renderReport()` only displayed `#report-upload` when `action === 'resume'`, `sanitizeRun()` only allows `['inspect', 'details', 'resume', 'security-code']`, and running `resume` on a Workday details-only step (`canAttachResume: false`) would return `interrupted` and clobber the completed `details` result.
   - **Resolution:**
     - `actions.js` preserves `workdayScope` on frame results so `prepareApplication()` only runs Phase 2 (`resume`) when a local PDF is selected AND the current page/step supports attachment (`canAttachResume !== false`).
     - `checkpointRun(action, job, report = state.report)` checkpoints Phase 1 (`details`) with its own `diagnosticId` before injection, and checkpoints Phase 2 (`resume`) with a second `diagnosticId` before injection.
     - `mergePrepareReport(detailsReport, resumeReport)` combines the `details` field results with the `resume` attachment outcome, removes `'Use Attach résumé separately'` from `skipped` when the PDF is attached, and `renderReport()` displays `#report-upload` whenever any frame has a non-`'not-requested'` `resume` result.

2. **`[P1]` `sidepanel.test.mjs:632-654` measurement of `#scan-privacy` when `#advanced-tools-drawer` starts closed**
   - **Issue:** When `#scan-privacy` is inside a closed `<details>`, `getBoundingClientRect().top` is `0`.
   - **Resolution:** Measure `#advanced-tools-drawer` while closed and open `#advanced-tools-summary` before measuring `#scan-privacy` in the layout hierarchy test.

3. **`[P2]` Preserve `[aria-label="Local assistant connection"]` inside `#advanced-tools-drawer` and `setBusy()` slice boundary**
   - **Issue:** `setBusy(busy)` filters out controls inside `[aria-label="Local assistant connection"]` so concurrent startup checks in `setupLocalPanel()` do not leave `#local-connect` permanently disabled.
   - **Resolution:** Keep `aria-label="Local assistant connection"` on the inner Local assistant section inside `#advanced-tools-drawer`, and preserve the exact `function setBusy(busy) {` to `\nfunction updateActionButtons()` slice boundary in `sidepanel.js`.

4. **`[P2]` Situational `#security-code-section` visibility must prioritize active tab provider**
   - **Issue:** Checking `state.job.provider` unconditionally would keep the Greenhouse security code box visible after switching to an Ashby/Workday/Lever tab if the old draft was Greenhouse.
   - **Resolution:** Resolve the active tab's provider first (`matchesActive && state.job.provider ? state.job.provider : providerFor(activeUrl || state.job.url)`) and only inspect `state.report` for security-code fields when `!differentApplication`.

5. **`[P2]` `#job-details-drawer` auto-collapse must not trigger during manual typing or synthetic `toggle` events**
   - **Issue:** Setting `details.open = false` fires an async `toggle` event in Chromium, and typing into `#job-title` + `#job-url` would collapse the drawer mid-typing if `input` called `renderJob()` without marking the drawer touched.
   - **Resolution:** Mark `state.jobDrawerTouched = true` on `#job-summary-toggle` click and `#job-form`/`#job-notes` `input`; reset `state.jobDrawerTouched = false` only on capture/tab-sync (`#capture-job`, `prepareApplication`, `runAction`, `passAndCloseTab`).

6. **`[P3]` Keep `#prepare-application` enabled when no PDF résumé is selected**
   - **Issue:** Users without a saved PDF or on a details-only step still need `Prepare application` to fill details.
   - **Resolution:** Set `$('#prepare-application').disabled = state.busy;` in `updateActionButtons()`.

7. **`[P1]` `#submit-close-tab` must never close the tab if saving fails, must not close an unrelated active tab, and needs a URL-slug fallback when `company` is blank**
   - **Issue:** `recordApplication('submitted')` throws if `title`, `company`, or `url` is blank, and returns early if an outcome already exists. Closing the tab unconditionally or when `activeTab.url` does not match `state.job.url` could close the wrong tab or lose unsaved state.
   - **Resolution:** `submitAndCloseTab()` auto-populates missing `title`/`company`/`url` from `captureCurrentJob()` or the active tab's title and ATS URL slug (`deriveJobFallbacks`), saves the `submitted` record first, and only calls `chrome.tabs.remove(activeTab.id)` when the save succeeds and `sameApplicationUrl(state.job.url, activeTab.url)` holds.

8. **`[P1]` Mock `chrome.tabs.remove` in `extension/tests/sidepanel.test.mjs` without adding `"tabs"` permission to `manifest.json`**
   - **Issue:** `sidepanel.test.mjs` did not define `chrome.tabs.remove`, and `workday-extension.test.mjs:28` enforces `permissions: ['storage', 'sidePanel', 'scripting', 'activeTab', 'nativeMessaging']`.
   - **Resolution:** Add `remove: async tabIds => { globalThis.__extensionCalls.push({ tabsRemoved: [tabIds].flat() }); }` to `chrome.tabs` in `sidepanel.test.mjs` while keeping `manifest.json` permissions unchanged.

9. **`[P2]` `#pass-close-tab` and `#close-handled-tabs` must guard against `undefined` `tab.url`, non-web tabs, and overwriting non-`started` statuses**
   - **Issue:** Unpermitted or `chrome://` tabs have `undefined` or non-http(s) URLs that throw in `normalizeJobUrl()`, and passing on a tab that was already `submitted` must not overwrite its status to `withdrawn`.
   - **Resolution:** Guard all tab URLs with `validWebUrl(tab.url)` and `try / catch` around `normalizeJobUrl`, only mark `existing.status === 'started'` records as `'withdrawn'` on `#pass-close-tab`, and skip pinned or non-matching tabs in `#close-handled-tabs`.
