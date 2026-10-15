# Career workspace — extension 0.9.0

Open **Career memory, time capsules, and unified inbox** from the extension's
side panel. The workspace opens in its own extension tab and uses the same
profile, résumé library, and application tracker as the panel. It needs no
Python server, AI account, new browser permission, or network service.

## Career memory

Save a real work account with its timeframe, your contribution, details, skills,
useful question types, source reference, and truth boundaries. Check the wording
approval box when you have reviewed the account. Editing clears approval and
increments the content revision. Wording approval is self-review, not external
verification or an assessment of qualifications.

Search using an application question or role context. Results explain matching
terms; related troubleshooting, customer, deployment, leadership, and development
terms are grouped. This is local text retrieval, not an AI-generated fit score.

Import a legacy `true_accounts.json` (`accounts` list), a story array, or an
exported `jobpilot.stories/v1` packet. Preview it, stage it in Inbox, then accept
the accounts you want. Imported approvals are cleared. Review the wording in
Career memory afterward. The vault holds at most 500 stories; imports are bounded
to 2 MiB, and structured fields have individual limits.

Export matching accounts as JSON or Markdown to give an assistant durable
context. The packet retains sources, truth boundaries, review state, and
revisions. It tells the recipient to treat quoted material as data and preserve
missing facts. **An ordinary chat cannot read Chrome's local storage.** Export
and share a packet explicitly; this release does not silently synchronize chats,
read their history, or open evidence links. No private account is bundled.

## Application time capsules

After submitting an application, choose its tracker record or enter its company,
role, URL, and submission time. Add the materials you actually used:

- A saved résumé or the exact PDF file (up to 10 MiB).
- The posting text and final question/answer pairs, pasted from your record.
- Any wording-reviewed career stories you want to preserve with it.
- A confirmation screenshot, PDF, TXT, or EML receipt (up to 5 MiB).
- Submission notes.

Review the preview, confirm that you submitted and reviewed the materials, then
seal the capsule. Missing materials remain visibly missing. This does not scrape
final answers, submit a form, infer a submission from autofill, or certify receipt
by an employer. An attached receipt is preserved evidence to inspect yourself.
Saving a capsule does not change the live tracker; use Inbox or the panel to
record progress separately.

Sealing stores independent copies of résumé and receipt bytes and snapshots the
selected story revisions. Later changes or deletion in the source libraries do
not alter those copies. Exact answer and posting whitespace is retained. A story
changed after the preview must be reviewed again before sealing. Capsule records
have no update operation. Correct a mistake by recording a new capsule and
explaining the correction in its notes; keep the original history.

Open a capsule to read saved materials, download the exact files, or export the
complete `jobpilot.capsule/v1` JSON. SHA-256 checksums cover its content and
attachments, and are checked when opening or importing. These checks detect
changes; they are not a digital signature or proof of employer delivery.
Standalone imports are bounded to 24 MiB. Imported capsules do not mark a role
submitted in the tracker.

## Unified inbox

Import XLSX, CSV, TSV, or JSON, or paste JSON/CSV/TSV. JobPilot previews the rows
before staging them. Stage creates pending records and never changes the tracker.
The current tracker is displayed on the same screen, including records created
in the extension panel.

JSON accepts an array or one `jobs`, `applications`, `cleared_jobs`, `clearedJobs`,
or `records` array. Column aliases include `Employer`, `Role`, `Official role
URL`, and `Submission status` from DOT exports, plus ordinary `company`, `title`,
`url`, `status`, and `notes`. Every job needs a company, role, and complete HTTP(S)
URL. Additional columns become source notes. Text files must be UTF-8.

XLSX reads ordinary workbook cells, shared strings, and cached formula results
across job-bearing sheets. The URL must be cell text; display-only hyperlink
labels are not resolved. It never executes formulas or fetches linked resources.
External workbook references, encrypted files, ZIP64, malformed archives, and
DTD/entity declarations are rejected. Imports are limited to 5 MiB, 20 MiB total
uncompressed XLSX content, 500 ZIP entries, 2,000 job rows, and 100 columns.

Review each pending item's source file/sheet/row, original status, notes, and
current saved record. Accept a new job, explicitly use an imported update, keep
the saved record, or dismiss the import. Conflicting statuses are never chosen
automatically. Unknown statuses remain `started` with the original wording and a
note; `prepared`, `cleared`, and `paused; not submitted` do not become submitted.
Imported statuses are source claims awaiting your review, not delivery proof.

Repeated identical source records are skipped. Review decisions and their prior
values remain in inbox history. If another panel changes a record during review,
the stale decision is rejected so you can refresh and compare again. The worker
serializes all storage operations. Tracker writes and inbox decisions span two
browser stores: if the tracker saves but recording the decision fails, the UI
explicitly reports that outcome and asks you to refresh before retrying.

## Backup and restore

Under **Backup and restore**, export the career workspace to retain stories,
complete capsules (including files), and inbox history. This is separate from
the existing side-panel profile/tracker backup, which still omits library PDF
bytes. Backups contain private information; keep them somewhere appropriate.

Restore previews the counts, validates the entire input before writing, and
preserves existing records. Capsule ID/checksum conflicts fail the whole restore.
Stories and inbox updates return to pending review; imported wording approvals
and prior submission decisions are not accepted as new local decisions. Repeated
identical restores do not add duplicate pending items. Imports are bounded to
50 MiB; use individual capsule exports for larger archives.

State lives in an IndexedDB database private to this extension identity, with
all requests routed through the existing trusted-extension worker broker. It is
not a cloud backup. Removing the extension or browser profile can remove it.
Use the existing extension folder and Reload when updating to preserve identity.

## Verification

```sh
npm ci --ignore-scripts
node --test extension/tests/*.test.mjs
python3 -m unittest tests/test_extension_package.py
```

Checks use synthetic data, temporary browser profiles, real IndexedDB, local
XLSX fixtures, and an isolated MV3 extension. They cover review invalidation,
exact snapshots, attachment persistence, stale inbox reviews, restore validation,
repeated imports, responsive layouts, keyboard navigation, and automated WCAG AA
checks. They do not exercise real employers, personal application records,
physical phones, or screen-reader speech.
