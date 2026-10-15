# JobPilot extension

JobPilot keeps your profile, résumés, and application records in a Chrome side panel beside the job you are viewing. The extension works locally: the profile and tracker do not require the Python server or a hosted account.

Version **0.11.0** adds **Scan privacy on this page** in Apply. Open the job's
application step, click the JobPilot toolbar icon to grant page access, and scan
before sharing answers. The report shows exact question wording, required or
optional responses, decline choices, and employer-provided help. It reads visible
field metadata without reading entered answers, filling, advancing, or submitting.
The last scan stays local and can be exported. Rescan each step: later, hidden,
conditional or inaccessible embedded questions remain outside coverage. Existing
permissions are unchanged; no new host access or Python service is required.

For comparing public Greenhouse forms by link, the companion product supports
`python -m product.main --privacy`. Other ATS pages use the extension's visible
scan; an uninspected step is never treated as privacy-safe.

Version **0.10.0** includes a **Career workspace** link in the side panel: searchable,
reviewed career stories with assistant exports; immutable application time
capsules with independent résumé/receipt copies; and an inbox for reviewing XLSX,
CSV, TSV, and JSON tracker imports. See [the workflow and limits](WORKSPACE_GUIDE.md).
Use **Backup and restore** in that workspace for complete capsule files. The
existing profile/tracker backup below still omits library PDF bytes.

The 0.9.1 button audit fixes stale captures and import previews, interrupted
actions after tab/page changes, busy controls, and import retry/refresh behavior.
See the [tested controls and page coverage](BUTTON_AUDIT.md).

## Install

1. Build the unpacked extension with `python3 scripts/package_extension.py --output ~/job-search/JobPilot-Extension --zip`. Use the output directory, not this source directory.
2. In Chrome, open **Extensions → Manage Extensions**, turn on **Developer mode**, and choose **Load unpacked**.
3. Select the generated extension directory. Pin JobPilot if you want its button visible.
4. Click the JobPilot button to open the side panel. Review your imported profile and choose a résumé before filling an application.

Chrome extension settings and installation are user actions. When updating an existing installation, reload its entry on the Extensions page.

For a private package that starts with your verified details and a PDF, add `--profile /absolute/path/verified-profile.json --resume /absolute/path/resume.pdf`. The first launch copies this optional seed into local storage. It does not replace an existing edited profile or tracker. Keep a seeded package private.

## Apply with the side panel

- **Profile:** edit verified contact details, work authorization, work history, and education. An unanswered authorization question remains unknown; it does not become a yes or no by default.
- **Résumés:** add PDFs and choose which version to use for the current application.
- **Current job:** review the captured company, role, and URL before saving them. Inspect the form, fill supported details, then review the remaining fields.
- **Tracker:** save an application as started. After the employer shows its submission confirmation, mark it submitted yourself. Filling fields or attaching a file does not mean an application was submitted.

The extension never presses the employer's submit button. Review every answer and attachment on the employer's page before submitting. Consent, signatures, demographic questions, salary expectations, and job-specific claims require your answer.

## Portal coverage and first-release limits

Automatic form filling supports the allowed Greenhouse, Lever, and Ashby hosts listed in `manifest.json`, plus public Workday candidate job routes described below. Other sites use manual profile copying and application tracking; they do not get an automatic-fill success message.

Existing answers and attachments are preserved. Work and education fields fill only when a clearly labelled, numbered section can be matched to the same verified profile row. Ambiguous or unsupported repeaters require manual copying. The extension does not add missing work-history rows or navigate multi-step application forms.

### Workday applications

Version 0.10.0 supports public candidate job routes on `tenant.wdN.myworkdayjobs.com` and `wdN.myworkdaysite.com/[locale/]recruiting/tenant/site/job/...`. It uses the existing **activeTab** permission: open the job or application step, click the JobPilot toolbar icon on that tab, then capture or inspect. No new persistent host permission is added.

Capture the job before opening its application. After you sign in or choose to apply manually, use **Inspect fields**, **Fill details**, and **Attach selected résumé** on the current step. My Information and My Experience use verified contact and existing history controls; the résumé step supports an explicit local PDF upload. Review every result, advance the step yourself, and inspect again. Workday may replace the form without changing the URL; JobPilot stops an in-flight action when the step changes.

Sign-in, account creation, verification, application questions, disclosures, demographic answers, attestations, and final submission stay under your control. Unknown or translated labels, ambiguous history rows, and unsupported date/phone formats remain manual. Existing answers and attachments stay intact. The report distinguishes a selected file from a résumé that the page displays as accepted.

Job identities ignore locale, tracking parameters, and `/apply/...` suffixes while retaining the host, tenant, career site, and job path. Older tracked Workday records retain their IDs, notes, résumé choices, and status when reopened from another application step. Sealed capsules keep their original evidence.

Version 0.8.1 rechecks the live form between fields, handles delayed Greenhouse location suggestions and country/state aliases, and discovers fields revealed by earlier selections. It waits up to 3.5 seconds for an associated dropdown and limits a details action to about 20 seconds. A changing, ambiguous, or rejected control appears in the review report rather than stopping every other supported field. User edits made during autofill are preserved.

Version 0.8.2 recognizes Ashby's question-style contact labels, unassociated autocomplete inputs, CSS required markers, and visible choices backed by transparent native inputs. Yes/No button groups appear in inspection and required-field review; JobPilot leaves those answers to you. Save a preferred name and street address in **Profile** before reusing them. Missing values remain blank. Ashby company names can also be captured from the page title when JobPosting metadata is absent.

An interrupted action retains the frame results already returned. If the panel closes mid-action, reopening it shows that the final result is unknown. Inspect the page and retry **Fill details** to continue with empty fields. JobPilot does not automatically replay an interrupted action or switch application steps. A résumé action remains separate.

To update an unpacked installation without creating a second extension identity, replace its packaged code in the same folder, then **Reload** JobPilot in Chrome's extension manager. Keep `initial-data.json` and `initial-resume.pdf` if present; runtime profile, résumé library, and tracking data are stored in Chrome, not those seed files. Keep a backup of the old package and export your workspace before removing the extension. Updating the folder does not activate new code until Reload.

A résumé result of **prepared** means a file was placed in the upload control. **Accepted** means the page displayed attachment evidence without a detected upload error; the employer's final application review remains the authority. An unlabelled or unrelated upload control is never treated as a résumé field.

## Run history and problem reports

Version 0.8.3 adds **History**. Inspect fields, Fill details, and Attach résumé automatically save a local record before starting and update it as frame results return. The last 50 runs survive closing the panel, clearing the current report, and later attempts. When updating from an older version, the most recent report is imported once. Earlier overwritten reports cannot be recovered.

Failed controls, unmapped questions, interruptions, and unsuccessful or unconfirmed uploads appear under **Open problems**. Expected manual questions and missing profile facts appear under **All runs** without being marked as bugs. If a result looks wrong despite a success message, choose **Report a problem** beneath the report. **Mark resolved** hides a reviewed case from open problems; **Reopen** restores it.

Use **Export run** for one case or **Export open problems** for the current backlog. The JSON contains a run ID, time, version, public job link, field labels, outcomes, and fixed reason codes. It excludes form answers, profile values, résumé names and contents, raw exceptions, HTML, URL query strings and fragments. Contact-like text in labels is redacted. Unexpected exception details become an `unknown` code. Nothing is sent automatically; exports are separate from your profile backup. Logs are bounded to 200 field entries and eight frames per run, with truncation indicated.

The history is evidence from extension actions, not a record of employer submissions, page validation after manual edits, or activity while JobPilot is idle. It cannot diagnose an employer-side rejection by itself. If diagnostic storage is unavailable, the action result stays visible and JobPilot does not retry the form automatically.

## Data and backups

Profile, résumé files, and application records stay in the extension's local browser storage. They are not a cloud backup. Export a backup before reinstalling or removing the extension; import only a backup you trust, and review its contents. Backups contain profile, application records, and résumé metadata, but **omit PDF file bytes**. Keep the original PDFs and add them again after restoring in another browser. Filling a supported form intentionally shares the selected details or résumé with that employer's page.

The default package contains no personal profile or résumé. Optional private seed files and later imported data belong outside Git. Do not commit private exports, PDFs, or application records to this repository.

## Verification

```sh
npm ci --ignore-scripts
npx playwright install chromium
node --test extension/tests/*.test.mjs
```

Browser tests use a separate Playwright Chromium profile and intercepted local fixtures. They do not access installed Chrome extensions, Chrome settings, or live employer applications. Tests cover conservative field matching, delayed dropdowns, rerenders, edits during autofill, interruptions, unanswered required controls, attachment evidence, local storage, and the side-panel workflow. Portal behavior can change; a passing fixture suite does not guarantee every live employer form behaves identically.
