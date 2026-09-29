# JobPilot extension

JobPilot keeps your profile, résumés, and application records in a Chrome side panel beside the job you are viewing. The extension works locally: the profile and tracker do not require the Python server or a hosted account.

## Install

1. Build the unpacked extension with `python3 scripts/package_extension.py --output ~/job-search/JobPilot-Extension --zip`. Use the output directory, not this source directory.
2. In Chrome, open **Extensions → Manage Extensions**, turn on **Developer mode**, and choose **Load unpacked**.
3. Select the generated extension directory. Pin JobPilot if you want its button visible.
4. Click the JobPilot button to open the side panel. Review your imported profile and choose a résumé before filling an application.

Chrome extension settings and installation are user actions. When updating an existing installation, reload its entry on the Extensions page.

For a private package that starts with your verified details and a PDF, add `--profile /absolute/path/verified-profile.json --resume /absolute/path/resume.pdf`. The first launch copies this optional seed into local storage. It does not replace an existing edited profile or tracker. Keep a seeded package private.

## Apply with the side panel

- **Profile:** edit verified contact details, work authorization, work history, and education. An unanswered authorization question remains unknown; it does not become a yes or no by default.
- **Résumés:** add PDFs and choose which version to use for the current application.
- **Current job:** review the captured company, role, and URL before saving them. Inspect the form, fill supported details, then review the remaining fields.
- **Tracker:** save an application as started. After the employer shows its submission confirmation, mark it submitted yourself. Filling fields or attaching a file does not mean an application was submitted.

The extension never presses the employer's submit button. Review every answer and attachment on the employer's page before submitting. Consent, signatures, demographic questions, salary expectations, and job-specific claims require your answer.

## Portal coverage and first-release limits

Automatic form filling supports the allowed Greenhouse, Lever, and Ashby hosts listed in `manifest.json`. Other sites use manual profile copying and application tracking; they do not get an automatic-fill success message.

Existing answers and attachments are preserved. Work and education fields fill only when a clearly labelled, numbered section can be matched to the same verified profile row. Ambiguous or unsupported repeaters require manual copying. The extension does not add missing work-history rows or navigate multi-step application forms.

A résumé result of **prepared** means a file was placed in the upload control. **Accepted** means the page displayed attachment evidence without a detected upload error; the employer's final application review remains the authority. An unlabelled or unrelated upload control is never treated as a résumé field.

## Data and backups

Profile, résumé files, and application records stay in the extension's local browser storage. They are not a cloud backup. Export a backup before reinstalling or removing the extension; import only a backup you trust, and review its contents. Backups contain profile, application records, and résumé metadata, but **omit PDF file bytes**. Keep the original PDFs and add them again after restoring in another browser. Filling a supported form intentionally shares the selected details or résumé with that employer's page.

The default package contains no personal profile or résumé. Optional private seed files and later imported data belong outside Git. Do not commit private exports, PDFs, or application records to this repository.

## Verification

```sh
npm ci --ignore-scripts
npx playwright install chromium
node --test extension/tests/*.test.mjs
```

Browser tests use a separate Playwright Chromium profile and intercepted local fixtures. They do not access installed Chrome extensions, Chrome settings, or live employer applications. Tests cover conservative field matching, unanswered required controls, attachment evidence, local storage, and the side-panel workflow. Portal behavior can change; a passing fixture suite does not guarantee every live employer form behaves identically.
