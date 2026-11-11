# Greenhouse field reliability — revision 2

Date: 2026-11-11
Source baseline: `43fe913461cea5b528e5c34aa8e1e616a2e95fad` (`main`)
Owner request: Greenhouse struggles with country-code fields, tables/repeating rows, and multi-step forms.
Revision 2 resolves the four material blockers in the independent revision-1 review.

## Goal and user experience

Improve filling of the current, visible Greenhouse application step while preserving applicant control. JobPilot should fill a supported split-phone widget and clearly matched existing work/education rows, then accurately report what remains. The applicant continues to review every answer and perform navigation, row creation, and submission.

## Scope

1. Recognize dedicated phone country/dialing-code controls, including labels such as `Country code`, `Phone country code`, and `Dialing code`, plus `autocomplete="tel-country-code"`.
2. Fill supported split-phone fields without putting the international prefix into a national-number input.
3. Accept phone values whose formatting changed but whose complete digits remain identical; changed digits and extensions remain failures requiring review.
4. Extend work/education context to clearly labelled semantic table rows and explicit repeating groups; fix nearest-fieldset and hidden/template-row indexing problems.
5. Preserve deliberate applicant clearing and stop rather than fill a replacement application step.
6. Add intercepted synthetic Greenhouse fixtures for each supported case and conservative failure behavior.

## Explicitly out of scope

- Clicking employer `Next`, `Continue`, `Add another`, or submit controls.
- Creating missing work/education rows or filling later steps automatically.
- Live employer-form automation or submission.
- Adding a new persisted profile field or changing backup/profile schemas.
- Broad support for arbitrary unlabelled tables or non-Greenhouse portals.

## Consequential decisions and data flow

### Split phone and dialing code

- Parse split-phone values through one grammar only: `^\+(\d{1,3})\s+(\d[\d\s().-]*)$`. This supports clearly separated values such as `+1 512...` and `+44 20...`. It rejects missing prefixes, unseparated values such as `+4420...`, a prefix over three digits, extensions, letters, and an empty national part. Do not write either part of a split-phone widget when parsing fails. Ordinary non-split phone fields retain their current behavior.
- Derive the dialing prefix and national number from that single parse of verified `profile.phone`. Do not infer a prefix from residence country and do not add another profile source of truth.
- Recognize a dialing-code control from an explicit code label/autocomplete token. Also classify a generic `Country` control as a dialing-code control only when its closest field wrapper contains exactly one visible telephone input and that control is not the telephone input. This covers the existing Greenhouse-style phone container without reclassifying ordinary address-country fields.
- Use a national number only for an explicit `autocomplete="tel-national"` input or a telephone input sharing that unambiguous split-phone wrapper with a dialing-code control. Otherwise retain current full-phone behavior.
- Use one dedicated dialing-code rule containing the parsed prefix and saved country. One option predicate is reused by native selection, combobox lookup, immediate acceptance, and final recheck.
- Accept code-only options only when their complete visible text is the expected prefix, allowing surrounding parentheses/whitespace. Named options may use `United States +1` or `United States (+1)` forms and must match both the expected prefix and the existing saved-country aliases. A matching country with the wrong prefix is not accepted. Central unique-match behavior remains: duplicate code-only `+1` options remain ambiguous and manual.
- A dedicated text dialing-code input receives the parsed prefix and must retain that exact prefix. Missing/ambiguous values remain manual.

### Phone formatting and applicant edits

- For phone fields only, compare complete digits after a full-string allowed-character check limited to digits and `+ ( ) . -` whitespace. Extensions, letters, missing digits, and changed digits do not pass. Native/ARIA validity still gates success.
- Record the exact accepted element after a successful fill. Retry an empty previously filled field only when that accepted element is disconnected and the candidate is a different connected element, which is positive rerender evidence. If the same connected element becomes empty, treat it as an applicant edit and never refill it during that run; the final recheck may require review.

### Current-step boundary

- Exclude controls inside `inert`, `aria-hidden="true"`, hidden, or otherwise invisible ancestors, including inactive rendered steps.
- At the start of a details run, capture the visible form/active-step root and initial visible controls. On every iteration, require the same root to remain connected and visible. If it is replaced, or if the complete initial control set is replaced by a new control set, stop with an interrupted/review result. Individual control rerenders and dependent fields revealed inside the same retained root remain supported.
- Location-URL changes retain the existing interruption behavior. After the applicant clicks Next/Continue, a new JobPilot action is required. No implementation clicks Next/Add/Submit.

### Tables and repeating history rows

- Support semantic `table`/grid structures only when an accessible caption/label clearly identifies work or education history. Each control still needs its own exact supported label/ARIA association; a `<th>` or cell position alone is not a field mapping.
- Count only visible candidate data rows. Exclude header rows, `template` content, hidden/inert/`aria-hidden` rows, and rows explicitly marked as templates. Numbered groups retain explicit row numbers; unnumbered rows use visible candidate-row order only.
- Search ancestors for the nearest recognized history group rather than letting an inner unrelated fieldset hide an outer `Work experience N` group. Reuse one history-context resolver for field rules and tokens so row identity cannot diverge.
- Multiple same-type unnumbered tables, duplicate explicit row numbers, or otherwise ambiguous row ownership remain manual. Do not infer field meanings from cell position and do not create rows.

## Affected files

- `extension/content.js`: field classification, option/value acceptance, run boundaries, retry behavior, and history context.
- `extension/tests/autofill-recovery.test.mjs`: split-phone, formatting, same-step, and applicant-clearing fixtures.
- `extension/tests/content.test.mjs`: semantic table, nested group, hidden template, and ambiguity fixtures.
- `extension/README.md`: update concrete supported behavior after verification, without claiming live-portal coverage.
- `docs/plans/2026-11-11-greenhouse-field-reliability-review.md`: independent review evidence and dispositions.

## Failure behavior

Missing/ambiguous international phone values, duplicate code-only options, unclear phone relationships, unknown history layouts, ambiguous row ownership, invalid values, changed controls, deliberate applicant clearing, and replacement steps remain skipped, failed, or interrupted with a review reason. No exception causes automatic navigation, row creation, or submission.

## Observable acceptance checks

1. Dedicated `Country code` native and React-style controls fill from `+1 512...`; an empty `tel-national` input receives only the national part. A `+44 20...` fixture proves the same parser handles a multi-digit prefix.
2. Missing prefixes, unseparated `+4420...`, and extension-bearing values write neither part of a split-phone widget.
3. `United States (+1)` matches a saved US/+1 profile; a US option with the wrong prefix and duplicate code-only `+1` options remain manual. Immediate and final acceptance use the same predicate.
4. `(555) 010-1200` is accepted for identical saved digits; changed digits, inserted letters, and extensions are not.
5. Clearing an already filled connected phone field during a delayed lookup leaves it empty and does not trigger another write. A disconnected replacement control may still be retried.
6. A labelled work-history table fills visible data rows in saved-row order while ignoring header and hidden/`aria-hidden`/marked-template rows. Existing edited cells remain preserved.
7. An inner `Dates` fieldset does not hide an outer numbered work-experience group. Unlabelled tables, multiple same-type unnumbered tables, and duplicate row numbers remain untouched.
8. A delayed lookup followed by same-URL replacement of the active step interrupts the run and leaves later-step controls untouched. Inert/`aria-hidden` steps are not inventoried.
9. No fixture clicks Next/Add/Submit. All existing Python and extension/swipe tests pass. Browser checks use intercepted local fixtures; locally, an ignored launcher override selects system Chrome without changing repository test defaults.
