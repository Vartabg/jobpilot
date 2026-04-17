# JobPilot — LinkedIn Job Application Assistant

A semi-automated tool that helps you apply to jobs on LinkedIn. It watches what you do, learns your preferences, and gradually starts filling in forms for you.

**How it works:** Opens Chrome, connects to LinkedIn, suggests answers for application fields, pauses at the final submit step for a review gate that includes the latest tailored resume draft, and learns from your corrections over time.

**New pre-check:** `jobpilot score --active` gives a quick fit score on the current LinkedIn job before you spend time applying.

**New sourcing step:** `jobpilot scan --greenhouse anthropic -k frontend` can search public ATS boards before you even open LinkedIn.

**New safety check:** `jobpilot doctor` reviews your profile, resume path, tracking DB, and local data health.

**New ATS draft step:** `jobpilot resume --active` generates a role-specific resume draft in markdown + styled HTML, with optional PDF export via `--pdf`; that latest tailored PDF is now the preferred upload candidate during apply flow.

**How to start:** `jobpilot start` (after installing with `pip install -e .`)
