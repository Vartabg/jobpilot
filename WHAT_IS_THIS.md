# JobPilot — evidence-led job search assistant

A local tool that finds and verifies roles, links job requirements to your evidence, and prepares materials for you to paste and submit by hand.

**How it works:** Scans direct ATS boards, builds an evidence-led company slate, drafts answers and tailored resume material, and records explicit outcomes. It never fills or submits a live ATS form.

**New pre-check:** `jobpilot score --active` gives an evidence-linked decision on the current LinkedIn job before you spend time applying.

**New sourcing step:** `jobpilot scan --greenhouse anthropic -k frontend` can search public ATS boards before you even open LinkedIn.

**New safety check:** `jobpilot doctor` reviews your profile, resume path, tracking DB, and local data health.

**New ATS draft step:** `jobpilot resume --active` generates a role-specific resume draft in markdown + styled HTML, with optional PDF export via `--pdf`; you review it and choose what to upload by hand.

**How to start:** `jobpilot queue --refresh` (after installing with `pip install -e .`)
