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
actionable issues. Live reload and verification follow when Chrome is free.

Live testing is limited to the approved Workiva role and saved contact details
and Software Engineer PDF. Street address and ZIP stay blank. No employer
submission, consent, account creation, or demographic answer is authorized by
this testing task. Any mandatory address gate limits which later steps can be
tested live.
