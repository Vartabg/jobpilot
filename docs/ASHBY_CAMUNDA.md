# Camunda Ashby application diagnosis

Inspected 2026-10-08 on JobPilot 0.8.1:
https://jobs.ashbyhq.com/camunda/987fb6e0-1e22-45e5-b152-57acbcb59cb2/application

The installed side panel reported four confirmed fills, fourteen skips and no
failures. Its remaining-required list contained only six fields. This was a
coverage and matching problem in the extension, not an observed rejection by
the employer's form.

## Causes and changes in 0.8.2

- The referral deny expression matched `referr` inside `preferred`. Anchor the
  referral term at a word boundary and map preferred name only from an explicit
  saved preferred name.
- Exact contact matching did not recognize the page's question-style phone and
  combined first/last-name labels, `State/Region`, or `Post Code`. Add bounded
  aliases rather than matching arbitrary questions containing those words.
- The extension had no saved street address fields. Add optional preferred name
  and address lines to profile editing, validated storage and backups. Never
  manufacture a missing value.
- Ashby's location input has no ID or direct label association. Its title is in
  `.ashby-application-form-field-entry`. Use that wrapper to resolve its label;
  continue selecting only a unique city/state/country match from its associated
  listbox. `aria-controls` appears when the menu opens.
- Radio and checkbox inputs use opacity zero with visible option wrappers.
  Inspect these visible choices, grouping radio options once per question.
  Exclude hidden sections.
- Required question titles generate an asterisk through CSS `::after`, absent
  from `textContent`. Inspect the title's rendered marker.
- Yes/No controls are buttons with `aria-pressed`, outside the old inventory.
  Report these groups and their selected state without pressing them. Their
  buttons have an implicit submit type, making manual review especially useful.
- Application pages can omit JobPosting metadata. Read the company from Ashby's
  `Role @ Company` document title when that metadata is absent.

## Review still needed

Production experience, AI agent experience, BPMN experience, legal status,
country-dependent work authorization, remote-work willingness, salary,
relationships, pronouns and consent need applicant review. Résumé attachment
remains a separate explicit action. The application advertises limits on repeat
applications; this investigation did not submit an application or test those
limits.

The regression fixtures use synthetic profile data and the observed public DOM
structure. They reproduce the missing mappings, skipped controls and incomplete
required-field report in 0.8.1, then verify 0.8.2's behavior. Live filling and
submission with personal data were not performed.
