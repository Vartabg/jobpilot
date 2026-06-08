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

1. **Source / score** the role with JobPilot (`scan`, `score`).
2. **Draft answers** into `data/answers/<company>/<role>.md` — verified evidence only, no inflation.
   Apply the supreme alignment filter and the Navy hard rule before drafting.
3. **Tailor the resume** and regenerate the PDF (`scripts/render_resume_html.py` for HTML, then the
   Playwright PDF step in `core/resume_tailor.py`). Every claim must be defensible against the live codebase.
4. **Generate the paste sheet**: `scripts/make_paste_sheet.py <answers.md>` writes `PASTE_SHEET.txt`
   beside it (fields labeled, answers backtick-stripped, links extracted). No browser involved.
5. **Human submits**: open the form in your *own* normal browser, paste the fields, upload the PDF,
   review, and click Submit yourself.

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
| `scripts/fill_comfyui_application.py` | example Ashby fill driver — **superseded** by the paste flow; kept only as a reference for the field-mapping approach. Do not use it to fill/submit a live form. |

## Answers file format

The markdown the paste-sheet generator expects:

- `# <Company> - <Role>` title line
- `Source: <application URL>`
- `## Form Values` — `Key: Value` lines (Name, Email, Resume path, yes/no pickers, "how did you hear", etc.)
- One `## <question>` section per free-text question, with the drafted answer as the body
