# Button and page audit — 0.12.2

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
| Disclosure preferences | Blank by default; saved choices persist locally and in profile backups. Fill details uses only an unambiguous offered veteran/decline choice and preserves existing answers. Agreements, signatures and final submission remain manual. |
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

## Live Workiva verification (October 16, 2026)

The signed-in Software Support Engineer R12637 application was used with explicit
permission for contact details and the selected local PDF. The toolbar grant,
Capture (role/company), Inspect, Fill, accepted PDF upload, privacy scan/filter,
Save as started and duplicate prevention, application filters/refresh, run
history/reporting and diagnostic exports were exercised through the installed
extension. An empty first name was filled; a repeated Fill preserved existing
answers. Street address and ZIP initially remained blank. Workiva's own validation
required both fields; the user later entered them for this application and
authorized continuing to My Experience. No employer submission, consent,
or demographic answer was made. Destructive library/profile operations and submission-status
changes remain covered by isolated tests rather than mutations of real records.

Live findings fixed in 0.11.2: repeated upload recognizes an accepted résumé when
Workday removes the file input; inspection recognizes a selected phone-code token
and required radio-question container; new History job links retain the public
requisition while stripping application suffixes and tokens. Privacy collection
excludes selection announcements from field help, and dialing-country-code labels
are distinguished from questions about residence or location.

Live findings fixed in 0.11.3: My Experience's generic upload field is associated
only with its verified local Resume/CV section; existing successful attachments
are preserved. Selected field-of-study tokens remain answered even beside empty
search inputs; unselected searches require a manual option choice. Workday
dropdown reports and privacy scans use fixed question labels rather than
answer-bearing button names. Privacy scans also recognize the role-less required
radio-question containers. Regression cases reject hidden, neighboring and
ambiguous evidence and assert that answer-state getters are never read by the
privacy collector.

My Experience Fill added three missing verified work-history facts and the saved
LinkedIn URL. Exact Bachelor of Arts/BA equivalence is restricted to verified
Workday education degree fields and a unique offered option; unrelated degrees
and duplicate equivalents are rejected. The correct degree was visible on the
live form. Repeat Fill preserved all 67 observed controls and reported zero
filled, 43 skipped, zero failed. Inspect found no empty visible required fields.
During the 0.11.3 pass, Workiva's own Save and Continue returned nine generic
Page Error entries on both attempts without identifying an invalid control.
Later steps were not reached in that pass. The application remained unsubmitted,
with the user's application-only address kept out of the local profile.

The 0.11.4 investigation identified HTTP 400 authorization failures in Workiva's
Network response. Restoring the expired account session in another tab allowed
the preserved My Experience form to save and reach Application Questions. No
answers or history rows were cleared to recover the draft.

Application Questions inspection now detects all three required choices using
their own fixed fieldset legends. Privacy captures all five question labels and
flags the optional pronoun(s) request. Scan metadata and findings were identical
before and after the user-approved answers were entered. Fill/Attach guarded the
step without changing any control, and Workiva saved it successfully.

Voluntary Disclosures inspection recognizes three demographic choices and the
required transparent native agreement checkbox, using its exact visible local
label. Privacy identifies gender, ethnicity and veteran requests. Fill/Attach
preserved every disclosure and agreement control. Consent and personal choices
remain with the user; the extension does not accept agreements.

## Saved disclosure preferences (0.12.0)

Automated checks cover blank defaults, strict preference validation, saving,
reload, clearing, and profile backup/import. Fill details applies only saved
preferences to an exact offered choice on supported forms. Tests distinguish
general and protected veteran questions, reject subtype and negative-wording
inferences, and prefer a specific protected category when one is offered.
Nondisclosure choices never become a negative disability or veteran assertion.

Workday question, disclosure, and self-identification steps receive only the
disclosure preferences. Contact data, names, signatures, dates, agreement
checkboxes, uploads, and navigation remain untouched on those steps. Review,
availability, and sign-in steps remain blocked. Fixtures cover native selects,
bounded native radio groups, and Workiva's listbox association that appears only
after opening. Existing selections, multiple selects, disabled or duplicate
choices, duplicate native select values, ambiguous radio groups, and changes to
the current question or step all preserve manual control. Diagnostic checks
exclude preference values and selected option text.

Live 0.12.0 Workiva testing selected the correct race nondisclosure and protected
veteran choices, while its report incorrectly classified those changes as
preserved answers. The fixed 100 ms confirmation check ran before Workday had
finished closing its menus. Version 0.12.1 waits for the expected answer and the
exact chosen menu to close, with a bounded wait and question/step guards. New
regressions cover delayed text and closure, subsequent user edits, step changes,
and a menu that stays open. An unconfirmed selection cannot count as filled.

The live Self Identify step exposed a second case: the disability answers are
three native checkboxes, while the required Date is split into three spinboxes.
Version 0.12.1 recognizes only the verified disability field/group, heading,
legend, and exact three offered labels as one question. With a saved decline
preference, only its unique nondisclosure choice may be selected. Existing
answers and all unrelated checkboxes are preserved. Date is inspected as one
required manual control; names, employee IDs, dates, and signatures are not
filled by this step's disclosure action. The privacy collector similarly emits
one required disability request with its offered decline option, using only
fixed public metadata. Hidden, ambiguous, altered, and incorrect-step structures
remain outside this exception.

Workday Capture also excludes the application/review body from its job-description
fallback. A canary that throws on review-text access verifies this on both host
families, while public posting-description and JobPosting metadata still capture.

Live 0.12.1 testing reached Workiva's final Review without submitting. The
disability decline was selected while Name, Date, and Employee ID stayed
untouched by autofill. Inspection grouped the required Name, Date, and Disability
status correctly; after manual Name/Date entry it found no empty required fields.
The privacy scan reported one disability request with disclosure optional.
Capture and Inspect worked on Review; Fill and Attach stopped there without
changing the form. The Review scan explicitly retained incomplete coverage.

That pass found a final reporting edge case: Workiva disables the two unchecked
disability answers after a choice is selected. The earlier strict enabled-choice
check then rejected its own successful decline. Version 0.12.2 recognizes that
verified answered group for inspection and preservation, while keeping the
all-enabled requirement before any new selection. URL, visibility, current-step,
question, and disconnected-control guards remain in place.
