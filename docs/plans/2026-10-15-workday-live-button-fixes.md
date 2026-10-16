# Live Workday button fixes

The signed-in Workiva application reproduced a page-access failure: Capture
reported a missing activeTab grant after repeated JobPilot toolbar clicks.
Chrome's automatic side-panel toggle opened the panel without invoking the
extension action. An explicit action listener now opens the panel immediately
inside that gesture; persisted automatic-toggle behavior is reset on startup
and installation. The manifest retains the same temporary activeTab permission
and seven persistent ATS hosts. No Workday host permission is added.

Review also reproduced a privacy initialization race. Capture could snapshot
Scan's disabled initial state while its saved-report read was pending, then
restore that disabled state permanently. Initialization now uses the shared
action lock and sets the final privacy controls after the lock is released.

The release preserves the privacy scanner from branch
`codex/privacy-extension-install` through commit `4658fb2`, including its test
expectation correction. Version 0.11.1 uses the same installed extension folder
and identity. All 28 public package files are replaced; seven private local
files remain byte-for-byte unchanged, with a complete folder backup.

Verification: 267 extension tests and four Python packaging tests pass. New
regressions cover immediate toolbar opening during pending startup, automatic
behavior migration, failed opening and retry, and privacy initialization during
both successful and failed storage reads. Independent code review found no
actionable issues. Chrome was reloaded and the toolbar grant and Capture were
verified on the real signed-in Workiva application on October 16.

Live testing is limited to the approved Workiva role and saved contact details
and Software Engineer PDF. The initial tests kept street address and ZIP blank.
The user later entered them specifically for this Workiva application and
authorized continuing into My Experience. Those entries are not reused or
copied into the local profile. No employer submission, consent, account creation,
or demographic answer is authorized by this testing task.

## Live follow-up: 0.11.2

The approved PDF displayed Workiva's explicit successful-upload marker. A repeat
click exposed the removed-input case; attachment detection now recognizes the
successful item in the unique current-step résumé wrapper before requiring a
file input. Existing, failed, pending, hidden, and unrelated uploads have separate
regressions. No existing file is replaced.

My Information testing restored an intentionally cleared first name and preserved
all existing answers on repeat. Street and ZIP remained empty. Inspection missed
the selected phone-code token and the radio question's required container; both
use the explicit Workday structures now. Radio answers remain manual. Workiva's
own Save and Continue validation required street and ZIP, along with two manual
questions, so the initial test stopped there. The user's later application-only
address entry allowed the follow-up below.

Final installed-build checks recognize the transparent native radio controls
behind Workday's visible option labels. The live inspection now matches all four
questions reported by Workiva's validation and no longer claims the selected
phone code is empty. A repeat résumé action displays Existing attachment
preserved. Privacy export, clear/rescan, report clearing, and the disabled
attachment button with no selected résumé were also checked live.

Privacy scanning exposed a selection announcement in field help and a dialing
prefix misclassified as location. Fixes are made in the canonical product sources
and regenerated extension files, with real collector-to-export regressions and
Python/JavaScript parity checks. New diagnostic runs retain only the bounded
public Workday requisition path, instead of opening the tenant homepage; application
suffixes, queries, and fragments are excluded. Known manual-step warnings also
retain explanatory reason codes.

Live local controls checked: Save as started/repeat deduplication, application
refresh and filters, run-history filters, reporting a real problem, diagnostic
single-run and open-problems export, and the career-workspace link. The workspace
opened memory, capsules and inbox, and its tracker showed the shared Workiva
Started record. Submission status and destructive library/profile operations
stay covered by isolated tests rather than falsifying or deleting real data.

## My Experience follow-up: 0.11.3

The user completed the address gate for this application and authorized further
testing. Selected Field of Study pills beside plain empty search inputs exposed
an overwrite risk. The collector now requires visible selection evidence within
the same current step, field, prompt, input, list, and item. Existing tokens are
preserved; empty or draft-only searches require a manual choice and have an
explanatory diagnostic reason code.

My Experience uses a generic file field inside a labelled Resume/CV group,
rather than `resumeUpload`. Detection now binds the unique local uploader to that
verified section, including its successful file item. The installed button now
reports Existing attachment preserved, with no duplicate upload or deletion.
Fixed local labels also replace answer-bearing Workday dropdown button names in
reports, history, and privacy metadata. Privacy capture includes closed dropdown
questions and role-less required radio groups, with no reads of answer state.

Live Fill confirmed three missing work-history facts, preserving all previous
values and selected studies. The precise Workiva LinkedIn question now maps to
the saved URL. BA/B.A/Bachelor of Arts equivalence is limited to verified Workday
education degree fields and a unique exact alias; Science, Fine Arts, Business
Administration, combined degrees and duplicate equivalents remain rejected.
The correct degree was observed on the live form; the live action classified
that changing selection as preserved. Repeat Fill left all 67 observed controls
unchanged and reported zero filled, 43 skipped, zero failed. Inspect found no
empty visible required field.

Workiva's Save and Continue returned nine generic Page Error entries, including
after one retry. No native-invalid or ARIA-invalid control was identified. This
prevents live testing of Application Questions and later steps; the application
stays on My Experience and is unsubmitted. Existing manual-step safeguards remain
covered by isolated browser tests. The user's street/ZIP entries are specific to
this application and are not copied to the extension profile.
