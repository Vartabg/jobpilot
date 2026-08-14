# JobPilot — Application Flow (automation drafts, human submits)

The durable recipe for applying to a role.
Refined 2026-06-08 after an ATS spam-flag incident.

## Principle

Automation drafts; the human submits.
The agent sources, scores, and drafts tailored answers plus a resume from verified evidence.
The human pastes those into their own browser and clicks Submit.
No automated browser ever fills or submits the live application form.

## Why (the spam-flag learning)

On 2026-06-08 a Comfy (Ashby) application was filled and the submit driven by a Playwright-controlled
"Chrome for Testing" browser.
Ashby flagged the submission as spam and required resubmission.
The same application, pasted and submitted by hand in a normal Chrome, went through cleanly.

ATS anti-spam systems detect automated browsers. The signals that tripped it:

| Signal | What it is |
|--------|-----------|
| `navigator.webdriver = true` | Playwright/CDP-controlled browsers announce themselves as automated |
| Fresh cookieless profile | A brand-new "Chrome for Testing" profile with no history is a bot fingerprint |
| Repeated loads | The live form was loaded ~5× in minutes (inspect + fill runs) from one IP |
| Instant bulk fills | ~2,600 chars filled instantly, with no human typing or mouse movement |

## The flow

1. **Source / assess** the role with JobPilot (`queue --refresh`, `score`). Standalone `score` may return a role-fit decision of `apply_now`, but that is not action readiness. A queue role becomes Apply-ready only when a current direct ATS listing passes the posting-evidence gate, the full description has enough evidence to assess, every configured fail-closed application-history source is current, and the evidence ledger corroborates the same role. Stale external history still permits search, but all positive recommendations become `Investigate`.
2. **Draft answers** into `data/answers/<company>/<role>.md` — verified evidence only, no inflation.
   Apply only the explicit policy and truth boundaries configured for this candidate.
3. **Tailor the resume** and regenerate the PDF (`scripts/render_resume_html.py` for HTML, then the
   Playwright PDF step in `core/resume_tailor.py`). Every claim must be defensible against the live codebase.
4. **Generate the paste sheet**: `scripts/make_paste_sheet.py <answers.md>` writes `PASTE_SHEET.txt`
   beside it (fields labeled, answers backtick-stripped, links extracted). No browser involved.
5. **Human submits**: open the form in your *own* normal browser, paste the fields, upload the PDF,
   review, and click Submit yourself.
6. **Record the explicit outcome**: log human reply, screen, interview, offer, rejection, withdrawal, or an intentionally classified no-response event. Ordinary silence is not automatically a rejection.

## Hard rule

Do not fill or submit a live application form with an automated browser (Playwright, CDP, a fresh
"Chrome for Testing" profile, etc.).
It trips ATS spam filters and risks misrepresenting the applicant.
The human submits from a real browser session — this is the human-in-the-loop gate for the submit step.

Reading a form once, read-only, to enumerate its questions is lower-risk — but prefer pasting the
questions to the agent over repeated automated loads of a form you intend to submit to.

## Artifacts

| File | Purpose |
|------|---------|
| `scripts/make_paste_sheet.py` | answers markdown → human `PASTE_SHEET.txt` (no browser) |
| `data/answers/<company>/<role>.md` | the drafted, approved answers (single source of truth) |
| `core/form_filler.py` | retired compatibility shim; the public live-fill entry point now fails closed |
| `data/opportunities.db` | append-only role evidence, assessments, source runs, and funnel events |

## Candidate evidence

`data/profile.json` stores explicit skills, target titles, overall experience, and optional named `domain_experience`. `data/true_accounts.json` may add versioned, truth-bounded life accounts with an `id`, `title`, `summary`, `details`, `skills`, and `truth_boundaries`. Each recommendation links back to those facts or stays unknown. Customer-facing energy, presentation ability, independent problem-solving, and analytical depth are therefore supported by real accounts—not inferred as personality traits.

## Decision and posting-evidence semantics

The decision axes stay separate so a strong work context cannot hide a missing mandatory qualification:

- `qualification_lower_bound`: supported mandatory requirements only.
- `work_context_match`: observed customer, presentation, autonomy, field, or collaboration evidence.
- `opportunity`: supported preferred requirements and responsibilities.
- `logistics`: location, travel, authorization, schedule, and similar constraints.
- `evidence_coverage`: how much of the posting has direct, adjacent, or contradicted evidence; unknown is not neutral.

Posting evidence uses deterministic grades and gates, not a confidence percentage or a claim about the employer's full corporate identity. Current direct ATS proof may be `recommend`; aggregator/recruiter-only evidence is `review`; stale or unavailable proof is `hold`; and closed, expired, conflicting, or scam-pattern evidence is `block`. Investigate the company separately before sharing sensitive information.

## External Gmail history

JobPilot does not authenticate to Gmail. If `data/policy.json` configures a Gmail application cache, create a fresh full-history JSON export with a separate read-only Gmail source and run `jobpilot gmail-sync <export.json>`. Records accept only company, title, status, an HTTPS role URL, one ISO event date/timestamp, and an optional opaque message ID; subjects, bodies, snippets, attachments, and arbitrary extra fields are rejected. The importer also rejects malformed, stale, future-dated, older, unsafe-URL, or partial-history exports and atomically replaces only the local cache. It never changes the source export or mailbox.

## Answers file format

The markdown the paste-sheet generator expects:

- `# <Company> - <Role>` title line
- `Source: <application URL>`
- `## Form Values` — `Key: Value` lines (Name, Email, Resume path, yes/no pickers, "how did you hear", etc.)
- One `## <question>` section per free-text question, with the drafted answer as the body
