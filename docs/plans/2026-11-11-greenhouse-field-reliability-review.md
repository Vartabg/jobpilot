# Independent design review — Greenhouse field reliability

## Review evidence

- Reviewer: fresh-session `general` subagent `ses_e34d77ebeffeb2Z1HJ0fuwL7hU`.
- Initial `review` subagent route `ses_e34d7a592ffe440iGJymgTe74L` failed before performing a review because its provider reported insufficient credits. It was not treated as review evidence.
- Completed reviewer status: **COMPLETE**.
- Reviewed plan: `2026-11-11-greenhouse-field-reliability.md`, revision 1.
- Required and actual source baseline: `43fe913461cea5b528e5c34aa8e1e616a2e95fad`; reviewer reported a match and only the plan as an untracked change.
- Reviewer reported reading the plan, `extension/content.js`, `extension/tests/autofill-recovery.test.mjs`, `extension/tests/content.test.mjs`, and `extension/README.md`.
- Static design/source review only; reviewer ran no tests and edited no files.
- Initial verdict: **NOT READY**.

## Material findings and lead dispositions

1. **International-prefix/national-number grammar was unspecified.** Current code writes the full saved phone (`extension/content.js:114`), and arbitrary `+44`/`+1` splits cannot share an unspecified parser. **Accepted.** Revision 2 defines one whitespace-delimited grammar, supports one-to-three-digit prefixes, rejects unseparated/extension values, and requires fixtures for multi-digit and negative cases.
2. **Dial-code classification/acceptance lacked one contract.** Existing country matching strips only an unparenthesized suffix and does not validate the phone prefix (`extension/content.js:128-142`); acceptance reuses the predicate (`extension/content.js:229-234`). The existing phone-country fixture does not vary the expected prefix (`extension/tests/autofill-recovery.test.mjs:95-105`). **Accepted.** Revision 2 defines generic-phone-container classification, a dedicated rule, shared native/combo/final predicate, parenthesized named options, wrong-prefix rejection, and duplicate code-only ambiguity.
3. **Current-visible-step boundary was not enforced.** Existing loop checks only URL changes (`extension/content.js:248-253`) and can discover new controls for about 20 seconds; visibility does not exclude inert/ARIA-hidden ancestors (`extension/content.js:6-13`). **Accepted.** Revision 2 requires retained step/form identity, whole-initial-control replacement interruption, inert/ARIA-hidden exclusion, and a same-URL delayed-step replacement fixture.
4. **A filled field could be refilled after deliberate clearing.** Existing retry logic retries a filled unanswered control (`extension/content.js:258`) and existing preservation fixtures do not cover clearing. **Accepted.** Revision 2 requires exact accepted-element identity and positive disconnected-replacement evidence before retrying; same-control clearing is not refilled.

## Optional findings and dispositions

- **Table headers are not input accessible names.** Accepted and clarified: controls require their own exact labels/ARIA; no cell-position inference; one resolver supplies rules and tokens.
- **Additional row ambiguity fixtures.** Accepted for hidden/ARIA-hidden/marked templates, multiple unnumbered same-type tables, duplicate numbers, and edited cells where practical in this slice.
- **Define phone punctuation.** Accepted through a full-string allowed-character contract and negative fixtures.
- **System-Chrome wording.** Accepted: local verification may use an ignored launcher override; repository test defaults remain portable and documented.

## Remaining limitations after revision 2

- No live employer behavior is verified; all new evidence must come from intercepted synthetic Greenhouse fixtures.
- Step replacement detection is conservative and may interrupt some whole-form rerenders; it must not claim to recognize every possible SPA transition.
- Only explicitly supported phone grammar and clearly labelled semantic history layouts are filled. Other cases remain manual.
- Revision 2 requires targeted re-review before implementation. This report alone is not a readiness pass.

## Targeted revision-2 re-review

- Reviewer: the same independent fresh-session reviewer, `ses_e34d77ebeffeb2Z1HJ0fuwL7hU`; no additional reviewer was launched.
- Completion status: **COMPLETE**. Final verdict: **READY**.
- Reviewed artifacts: plan revision 2 and this disposition report; reviewer rechecked the minimum source list as needed.
- Required/actual baseline: `43fe913461cea5b528e5c34aa8e1e616a2e95fad`; reviewer reported that production and listed test files remained unchanged and only the plan/review documents were untracked.
- Finding dispositions: deterministic phone grammar **RESOLVED**; shared dial-code predicate/classification **RESOLVED**; same-URL current-step enforcement **RESOLVED**; applicant-clearing retry boundary **RESOLVED**.
- Remaining material blockers: **none** reported.
- Non-blocking implementation caution: the existing generic `Country` phone fixture can also pass through the old country-only path. Add a generic-phone-container wrong-prefix assertion and ensure the shared `.phone-input` ancestor is considered, not only an inner `.field`.

## Lead attestation before implementation

I verified the reviewer returned a completed revision-2 report, the READY verdict applies to the matching plan filename/revision and source baseline, and the report identifies the actual files read. Implementation will follow the reviewed scope and stop for any material design change.

## Implementation code review

- Reviewer: fresh-session `general` subagent `ses_e34ca45a7ffeKs8AcuJ5hGoIws`.
- Reviewed the actual uncommitted diff against the required baseline, including production, tests, README, actions reachability, and the reviewed plan/report.
- Initial verdict: **HOLD** after four intercepted synthetic reproductions found implementation gaps not covered by the first fixtures:
  1. Generic `Country` inference incorrectly reclassified an ordinary contact fieldset. Fixed by requiring an explicit phone container for generic-country inference; added an ordinary Country + Phone regression.
  2. A replaced application form could be missed when another form remained connected. Fixed by tracking each initial control's closest step/form root; added a two-form replacement regression.
  3. ARIA `columnheader` rows shifted grid history indices. Fixed by excluding header-only `th`/`columnheader`/`rowheader` rows; added a two-row ARIA grid regression.
  4. Exact phone equality bypassed extension/letter rejection. Removed the bypass and added exact retained invalid-value regressions.
- An optional post-success disconnected-replacement retry test was also added to prove genuine rerender recovery remains supported.
- Targeted re-review completion: **COMPLETE**. Each finding **RESOLVED**; no new material blockers; final static verdict **SHIP**.
- Runtime evidence: focused phone/step suite 28/28; content/table suite 18/18; final serial JavaScript/browser plus swipe suite **104/104**; Python suite **1060 passed, 3 skipped**. `node --check` and diff checks are run as final gates.
- Local browser verification used intercepted fixtures and an ignored system-Chrome launcher override. Concurrent local browser files exceeded the initial command timeout; the final serial run completed successfully. This is a local resource/shutdown observation, not a failing assertion.
- Live Greenhouse behavior remains unverified; no live ATS form was filled or submitted.
