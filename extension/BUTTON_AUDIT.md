# Button and page audit — 0.11.1

This release tests the outcome of each control group with synthetic data and
isolated browser profiles. It does not submit applications or certify every
employer's current form. Public websites can change; a passing fixture is not
a guarantee of universal autofill support.

## Controls

| Controls | Verified result and failure behavior |
|---|---|
| Apply/Profile/Applications/History tabs, keyboard arrows, Home/End | Select the correct panel with matching focus and accessible state |
| Open career workspace | Uses Chrome's tab API to open the bundled workspace even when the active page cannot be injected; tested with the real extension worker |
| Capture tab | Reads the actual document, rejects missing/obsolete results and non-web documents, preserves manual fallback |
| Inspect fields | Reports the visible form or an incomplete/unsupported result; no empty false success after a failure |
| Fill details | Targets the verified current job and unique form; preserves existing answers; stops when tab/document changes |
| Attach selected résumé | Uses only the chosen local file; preserves existing attachment; distinguishes prepared file, rendered acceptance, and upload error |
| Clear report | Cannot erase a running action's report |
| Report a problem, Mark resolved, Reopen | Change the intended local run; updated controls remain usable after lists refresh |
| Export run/open problems | Download diagnostic records with private answers and file contents excluded |
| Save as started/I submitted this | Update one role record; retain progressed outcomes; never submit an employer form |
| View applications/Go to Apply, status/filter/refresh | Open the intended panel, show the requested subset, preserve notes and role identity |
| Profile Edit/Save/Add/Remove | Save intended structured fields and remaining history rows; preserve edits on failure; prevent edits during an in-flight save |
| Résumé add/choose/delete | Persist the chosen file, require deliberate deletion, allow retrying the same invalid file after an error |
| Profile backup export/import/confirm/cancel | Preview before import; cancel clears the pending file; reject invalid input without changing existing state |
| Memory New/Edit/Save/Cancel/Delete/Search | Correct story and review state; lock editable controls during save; reject stale edits |
| Memory JSON/Markdown export and import staging | Export matching stories; preview and stage without accepting imported review stamps; same-file error retries work |
| Capsule Add/remove answer, Review, Back, Seal, Refresh | Preview matches current edits; confirmation required to seal; refresh preserves explicit job/résumé choices |
| Open saved materials/download résumé/download receipt/export/import capsule | Recover the frozen bytes; validate checksums; duplicate import does not duplicate a capsule |
| Inbox file/paste preview/stage/cancel | Edits or parse failures invalidate earlier previews; repeated sources dedupe |
| Inbox Accept/Keep/Dismiss/Show all/Refresh | Review the intended source and current record; stale comparisons fail without overwriting progress |
| Workspace backup download/restore/cancel | Validate before writing; preserve existing data; repeated restore is idempotent |

## Pages and documents

Supported autofill hosts are the four Greenhouse domains, two Lever domains,
and `jobs.ashbyhq.com` already in the manifest. Workday candidate job routes on
`tenant.wdN.myworkdayjobs.com` and `wdN.myworkdaysite.com` use the existing
toolbar-triggered activeTab grant. Employee Workday, account/search pages, HTTP
variants, arbitrary employer sites, browser-internal URLs, and file/PDF
documents do not receive unsupported autofill injections. The local profile,
tracker, exports, and career workspace remain available independently of page
autofill access. Capture needs an ordinary job webpage and valid page access.

The toolbar explicitly invokes the extension action before opening the side
panel. Chrome's automatic side-panel toggle did not grant `activeTab` in the
live Workiva test, leaving the URL unavailable even after a toolbar click.
Opening the panel from Chrome's side-panel menu alone still requires clicking
the JobPilot toolbar icon on the intended application tab. No persistent
Workday permission is added.

The checks include plain listings, native inputs, React-controlled fields,
delayed location dropdowns, dependent fields, existing answers, multiple or
replaced controls, visible/hidden/ambiguous frames, unrelated third-party frames,
missing results, expired permissions, tab switches, pending navigation, and
changes while a dropdown or upload is waiting. Unclear or restricted controls
remain manual, with an explanation in the result.

Workday checks cover both candidate URL families, optional locale prefixes,
job/apply identity, contact controls, button/listbox dropdowns, existing work
and education rows, explicit résumé upload, and account gates. Step changes are
checked even when the URL stays the same. No Next, account creation, consent,
or submission action is automated. See the Workday section of README.md for
the current scope and manual steps.

Selectors were checked against the public Workday candidate apply-flow bundle:
<https://www.myworkdaycdn.com/wday/asset/candidate-experience-apply-flow/candidate-experience-apply-flow.min.js>.
Anonymous public pages establish current markup; synthetic browser fixtures
exercise writes without using an employer's saved application or personal data.

## Run the checks

From a repository checkout:

```sh
npm ci --ignore-scripts
node --test extension/tests/*.test.mjs
python3 -m unittest tests/test_extension_package.py
```

Browser tests include a real isolated MV3 installation, responsive layouts,
keyboard interaction, and automated accessibility checks. They do not use the
owner's active browser, profile storage, résumé, or employer applications.
