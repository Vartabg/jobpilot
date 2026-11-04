# Independent design review

Reviewed baseline: 9016a369d776a9267055d13ca0e7253aaf3f7b47. Plan revision 1 plus explicit corrections, incorporated into revision 2. Reading list: plan, extension/actions.js, content.js, manifest.json, background.js.

Preferred external Gemini route was attempted in an empty /tmp workspace using gemini-3.8-flash-high, supplied sources only. Initialization confirmed the model, but it timed out after two minutes with an empty response and print_timeout. This was an incomplete review, not a pass. Fallback fresh native reviewer /root/bridge_design_review used fork_turns=none, read the specified sources, and returned an actual completed report.

Material findings accepted:

1. Existing details can apply disclosure_preferences without a per-question personal-decision flag (content.js disclosureRule/details loop). Local details now strips that property; sensitive answers require an explicit single-question decision. Existing manual UI behavior is preserved.
2. Existing asynchronous guards bind URL/visibility but rediscover replacement controls. Add an internal localGuard closure and require bound scope/control identity/label/choice checks at each mutation checkpoint, including after waits. Replacement/conditional changes abort; reinspection follows. Add replacement/choice-change fixtures.

Transport correction accepted: cap final UTF-8 JSON at 900 KiB both directions and raw PDFs at 512 KiB; reject oversized inventories explicitly. Existing approved PDFs are below 5 KiB. Optional privacy fixture for selected-answer aria labels and minimal supported-tab metadata also accepted. Durable attempted-job storage further strengthens the no-retry contract.

Reviewer verdict: with the two amendments, ready to implement. Lead verified the completed response and source-grounded findings; revision 2 implements those amendments. No unresolved design blocker. Implementation/tests remain to verify. Review time: external timeout two minutes plus native review; useful findings: two; known false positives: none. This does not establish a productivity improvement.

## Implementation review and correction evidence

The same independent reviewer examined the implemented transport and extension.
Four concrete answer-path defects were accepted and fixed: generic fieldset
headings masking actual question labels; duplicate native option values selecting
the wrong visible choice; prevented checkbox/radio clicks being reported as
accepted; and custom choice buttons defaulting to native form submission outside
the nonce workflow. Fixtures verify exact selected state, privacy labels and zero
unintended submissions. The reviewer independently reran the two targeted
Chromium regressions for the last two P1 blockers: 2 passed, 0 failed, and
confirmed both resolved. No remaining blocker from those findings.

The Muse contributor supplied Python transport and initial tests using the pinned
muse-spark-1.3-contributor model (configured-model and terminal-completion events
verified). Lead verification exposed incomplete fake-Chrome test plumbing and
macOS temporary-path handling, which were corrected. The lead also tightened
owner/mode checks, stale-path preservation, response correlation and socket cleanup
to the host's own inode. Python transport and packaging: 18 tests pass. DOM and
answer/submission regression tests: 15 pass. Routing/connection focused tests:
18 pass. The new panel rendered at 320px with no horizontal overflow; Connect and
Disconnect work in the fixture. Live Chrome activation remains separately
verified through the installed host and applicant's existing extension.
