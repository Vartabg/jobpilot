# Install the privacy scanner

Goal: make the approved scanner usable in Garo's current JobPilot installation.

- Base the task on the clean, pushed Workday extension 0.10.0, then integrate
  the approved product scanner branch. Do not replace current features with
  the older main-branch extension.
- Generate a native metadata collector and analyzer from the product scanner;
  add an explicit side-panel scan with partial coverage and local export.
  Keep the permission manifest unchanged and send no profile or résumé data.
- Add a privacy-first launcher for the companion comparison page.
- Verify generated parity, native scan orchestration, packaging, product
  regressions and synthetic browser interaction before updating the active
  unpacked folder in place. Back up the folder and preserve private seeds.
- Reload the same extension entry, verify version and the scan button, and
  check existing profile/resume/tracker preservation. Retain Workday support.
- Finish through agent-task, push, and attach the reviewable PR.
