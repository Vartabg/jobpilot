# Contributing to JobPilot

JobPilot exists to give job seekers access to tools they couldn't build themselves. If you believe in that, this is the place to contribute.

## What would help most right now

The people this tool is built for are not developers. They're warehouse workers, veterans transitioning out, parents re-entering the workforce after a gap. They need things that require your skills:

**High impact, good first issues:**
- `good first issue` — bugs, selector fixes when LinkedIn/ATS layouts change
- `ux` — simplifying the CLI output, making error messages human-readable
- `windows` / `linux` — platform support so this reaches more people

**Bigger contributions:**
- A simple GUI wrapper so non-technical users don't need Terminal at all
- Support for more ATS platforms (Workday, iCIMS, SmartRecruiters)
- Automated tests to catch selector drift before users hit it

## How to get started

```bash
git clone https://github.com/Vartabg/jobpilot.git
cd jobpilot
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the tests:
```bash
pytest tests/
```

## Ground rules

- **Be honest about what works.** This tool is alpha. Don't oversell it.
- **Keep the human in the loop.** Features that remove user review/approval before submission won't be merged.
- **Write for the non-technical user.** Error messages, help text, and docs should be readable by someone who has never touched a terminal before.

## Reporting broken selectors

LinkedIn and ATS portals change their layouts regularly. If something stops working, open an issue with:
- What command you ran
- What you expected to happen
- What actually happened (paste the error)

That's it — no need for a full bug report.

---

*Every contribution here is a gift to someone who needed a break.*
