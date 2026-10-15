# Application privacy scan

Open **Application privacy scan** from JobPilot's navigation, or choose
**Scan application privacy** on a saved job. The scanner does not need your
profile, résumé, or answers.

Paste up to ten direct Greenhouse job links to compare public application
questions. Employer career-page wrappers are not guessed. A failed or unsupported
source remains an explicit incomplete result alongside the successful scans.

For Workday and other application pages, drag **Scan this application** to your
browser's bookmarks bar. Open the application step and use that bookmark. It
captures visible field labels, choices, required markers and associated help
text. Copy the resulting metadata into **Analyze captured field metadata** in
JobPilot. Repeat on later steps. Nothing is sent from the employer page, and
the scanner does not read entered answers, fill fields, attach files, sign in,
advance steps, grant permissions, solve challenges, or submit.

Some sites block bookmarks containing JavaScript; cross-origin embedded forms,
closed dropdowns, conditional questions, custom widgets without usable labels,
and later steps may not be inspectable. Open an embedded application directly
when offered by the employer. A scan never grants a clean bill of privacy.

Each finding shows exact wording, category, requiredness, decline choices and
employer-provided help. A required selection offering “Prefer not to answer”
is reported as **Response required; disclosure optional**. Missing requiredness
stays **Requirement unclear**. A city/state work-location question is distinct
from a full legal or street address. Greenhouse's hidden/derived location-widget
coordinates are described as technical metadata, not direct applicant requests.

The categories cover address/location, demographic information, health,
veteran status, identifiers, age, family, compensation history, financial
credentials, religion, politics, union membership, nationality/citizenship,
social-account access, media recordings, screening authorizations, and account
gates. Rules identify wording, not employer intent, legal compliance or actual
data use. Occupational experience involving other people is not automatically
treated as the applicant disclosing their own traits.

Reports remain in the page until downloaded as JSON. Captured metadata imports
reject answer keys. Review any manually pasted text before importing it.

Local API, using the normal authenticated session:

- `POST /api/privacy/scan`: `{ "urls": ["https://job-boards.greenhouse.io/company/jobs/123"] }`
- `POST /api/privacy/analyze`: `{ "source_url": "https://employer.example/apply", "coverage": "visible_form", "fields": [{"label":"Legal Address","required":true,"options":[],"description":""}], "notices": [] }`
- `GET /api/privacy/bookmark`: user-activated metadata capture bookmark.

Verification: `python -m pytest tests/product -q`; CI runs
`python scripts/check_privacy_ui.py --axe /path/to/axe.min.js` against synthetic
fixtures. Browser verification exercised exact labels, response-required decline
choices, required filtering, partial source failures, metadata import, answer
rejection, no capture leakage, radio grouping, dialog Escape/focus return,
skip link and reverse keyboard order. Axe reported zero violations in normal
and forced-color modes; 320px and 640px reflow had no horizontal overflow.
Temporary media/viewport overrides were restored. Real VoiceOver speech,
physical touch/orientation and actual browser-menu zoom remain manual release
checks listed in `TECH_DEBT.md`.
