# Local JobPilot controller

The controller connects this extension to a Python CLI through Chrome native
messaging and an owner-only Unix socket. No TinyFish, cloud browser, paid service,
HTTP listener, CDP connection or browser-profile copy is involved. Python 3.10+
and Chrome 116+ are required on macOS.

## Activate the existing installation

Keep the existing unpacked extension directory and its private seed files. Update
only the packaged code files, then click **Reload** on the existing JobPilot entry
in `chrome://extensions`. A new `nativeMessaging` permission enables the local
connection. The extension's stored profile, résumé library and history stay in the
same Chrome extension identity.

Register the host for the ID displayed by that entry (32 letters a–p):

```sh
python3 scripts/jobpilot_local.py install --extension-id YOUR_EXTENSION_ID
```

Installation writes a private persistent runtime under
`~/.local/share/jobpilot-local` and the exact-origin host registration under
`~/Library/Application Support/Google/Chrome/NativeMessagingHosts`. It does not
register another extension or allow wildcard origins. Open the JobPilot side
panel and click **Connect**. **Disconnect** stops the connection; reconnection is
manual. A missing host or stale socket is a hold, never an automatic retry.

```sh
python3 scripts/jobpilot_local.py status
```

The status command checks the socket; a successful `op: status` request verifies
the Chrome connection and returns the actual extension ID.
The native status also reports the loaded extension version. A `tabs` request
with `active: true` returns only the active supported job in Chrome's last-focused
window. Tab metadata includes loading, discarded and frozen state; unloaded tabs
are held until the applicant opens them. The controller never activates them
automatically.

## Commands

Write a private JSON request file, then send it once:

```sh
python3 scripts/jobpilot_local.py request --request /absolute/request.json \
  --output /absolute/result.json
```

`--profile /absolute/verified-profile.json` adds only that explicit profile to a
`details` request. `--resume /absolute/selected.pdf` adds only that selected PDF
to a `resume` request. No saved legacy profile or newest résumé is selected
implicitly. PDFs must have a PDF header and fit within 512 KiB. Final UTF-8 JSON
messages in either direction must fit within 900 KiB. Oversized inventories
return an error, not a silently partial export. Output files are mode 0600.

| Operation | Required request properties | Result |
|---|---|---|
| `status` | none | Native connection and extension ID |
| `tabs` | none | Minimal IDs/URLs/providers for supported ATS job tabs only |
| `open` | `url`, optional `active: true` | New tab ID; inspect after loading |
| `inspect` | `tabId`, `expectedUrl` | Document ID, snapshot, exact question inventory and provenance |
| `details` | job binding, `profile` | Conservative contact/history fill report |
| `resume` | job binding, `resume` | Attachment report: accepted/prepared/failed |
| `answer` | job binding, `questionId`, `value` | One explicit answer, never inferred |
| `prepare-submit` | job binding | One-use nonce, expiry and final button label |
| `submit` | job binding, `nonce`, `confirm: true` | One attempted click; no success claim |
| `receipt` | `tabId`, `expectedUrl`, optional `documentId` | Narrow visible employer confirmation, if observed |

“Job binding” means `tabId`, exact `expectedUrl`, `documentId` and `snapshot`
returned by the most recent inspection. Snapshots expire in five minutes and
are consumed by details/resume/answer, even on a hold. Inspect again after each
action. Preparation retains its snapshot for the nonce-bound submit; the nonce
expires after 30 seconds. Any changed answer, question, choices, form identity,
tab URL or document invalidates the binding. Fill stops when a form replaces
controls or reveals conditional questions; inspect those before continuing.

Example inspection:

```json
{"op":"inspect","tabId":123,"expectedUrl":"https://jobs.ashbyhq.com/example/req-123/application"}
```

Only Greenhouse, Lever and Ashby top-document job application routes are
supported. Workday retains its existing manual side-panel support. Embedded
forms, account pages, CAPTCHA/security challenges, ambiguous controls and
unrecognized submission/receipt pages are held for manual handling. The CLI
does not solve challenges or create accounts. Receipt URLs that no longer
identify the job are currently held rather than inferred.

Use exact offered choice labels. Existing answers are preserved unless
`replace: true` is supplied. A personal, demographic or legal answer additionally
requires `explicitPersonalDecision: true` on that one question, backed by the
applicant's actual decision. Local details never applies saved demographic
preferences. Required legal acknowledgments cannot be accepted implicitly.
The caller remains responsible for factual answers, employer authorization and
honoring applicant skips/recording preferences. An explicit flag is not evidence
that a person actually reviewed a policy.

## Submissions and recovery

Preparation checks required controls, employer validation, CAPTCHA, accepted
résumé evidence and a unique final button. A selected file without observed
acceptance is held. Submit rechecks the same state immediately before a single
click. The worker saves an attempted-job record in Chrome local storage before
dispatch. Disconnecting or reloading does not clear it. A timeout, navigation,
error or missing receipt means **unknown**, never “submitted.” Requests are not
replayed, and no command clears a submission attempt automatically.

Before an application, reconcile the canonical tracker, prior attempts and
employer receipts. Update the tracker only after an exact employer confirmation
or matching receipt is observed. If an attempt record is wrong, investigate the
employer state and record the evidence before a deliberate local-storage repair;
do not clear it simply to retry. A stale socket is likewise investigated before
removing it; the host never deletes an existing competing path.

## Employer-question research

Save successful inspection results in the private job-search evidence directory.
`questions` includes exact verified fixed labels, offered native choices,
requiredness, types and constraints; `source` includes timestamp, URL and evidence
method. Unverified custom labels are marked explicitly. Entered applicant values
and selected answers are excluded; `answered` records only a boolean. Snapshot
comparison state stays in the isolated document and is not exported.
Inspection also returns a separate `diagnostics` object with document visibility
and control-validation booleans/counts. It never opens menus or returns entered
values, selected labels or validation messages. Remove this operational supplement
from the sanitized research copy before importing it into the article ledger.

Import sanitized observations into the research ledger with employer/role,
category, exact wording, requiredness, evidence JSON pointer, source URL, date,
status and a scope caveat. Capture public posting requirements separately from
form questions. This inventory covers only the current visible form state, not
all possible conditional steps or a representative sample of employers. Verify
quotation accuracy and context before publication.
