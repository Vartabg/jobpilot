# JobPilot — LinkedIn Job Application Copilot

A semi-automated LinkedIn job application assistant that learns from your behavior. You stay in the loop — JobPilot fills fields, scores roles, drafts tailored resumes, and pauses for your approval before submit.

## Requirements

- macOS (the Chrome launcher is macOS-only for now; see Known Limitations)
- Python 3.11+
- Google Chrome installed at `/Applications/Google Chrome.app`
- A LinkedIn account you're willing to log in to

## Install

```bash
git clone https://github.com/Vartabg/jobpilot.git
cd jobpilot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
```

## First run

```bash
# 1. Launch Chrome with remote debugging and log into LinkedIn in the window that opens.
./scripts/launch_chrome.sh

# 2. Verify everything is wired up.
jobpilot doctor

# 3. Start the copilot (connects to the debug Chrome and waits for a job posting).
jobpilot start
```

## Commands

| Command | What it does |
|---|---|
| `jobpilot doctor` | Verify Chrome, local data, and active LinkedIn context |
| `jobpilot start` | Connect to Chrome and assist with applications (pauses before submit) |
| **`jobpilot queue --refresh --fresh`** | **Scan all ATS boards in `data/portals.json`, score with the Lane-C formula, dedup against your application history, return only roles you haven't touched** |
| `jobpilot queue --json --fresh` | Same, emitted as JSON to stdout — for piping into other agents/tools |
| **`jobpilot psyche`** | **Show your work-style profile + how it's scoring real jobs (top 8 by psycho-fit)** |
| **`jobpilot log <Company> --title <Role> --status applied`** | **Mark an external/manual application so future `--fresh` runs dedup it** |
| `jobpilot scan --greenhouse anthropic -k frontend` | Lower-level: scan one ATS board on demand |
| `jobpilot score --active` | Score the currently-open LinkedIn job |
| `jobpilot resume --active` | Generate an ATS-friendly tailored resume (`--pdf` for PDF) |
| `jobpilot profile` | View or edit your profile data |
| `jobpilot templates` | Manage answer templates for common questions |
| `jobpilot stats` | Show application statistics |
| `jobpilot history` | Recent application history |

You can also score/resume against a job description file instead of the active LinkedIn tab:

```bash
jobpilot score ~/Downloads/job-description.txt
jobpilot resume ~/Downloads/job-description.txt --output ~/Desktop/acme-resume.md --pdf
```

## Daily sourcing flow (v0.2)

```bash
jobpilot queue --refresh --fresh --no-open    # see fresh roles, scored + deduped
# pick one. verify location/comp/drug-policy manually.
# submit via the company's ATS (or jobpilot apply for LinkedIn flows)
jobpilot log "Acme AI" --title "Forward Deployed Engineer" --status applied
# next session, dedup just works
```

Each role gets a **0–100 fit score** across six dimensions:

| Dimension | Weight | Signal |
|---|---|---|
| Vertical moat | 25 | Industry tags in `core/queue_builder.py::MOAT_COMPANY_TAGS` cross-referenced to your lived industries |
| Tier fit (inverse prestige) | 20 | Curate `portals.json` to early-stage targets; title-based "enterprise/Fortune-500" hits dock it |
| Function coherence | 18 | "Forward Deployed", "Solutions Engineer", "Founding Engineer" titles score high |
| Skill overlap | 12 | Title-derived (AI/agent/full-stack/deploy/customer keywords) |
| Logistics | 10 | Role location, falling back to `COMPANY_HQ` lookup when the ATS field is empty |
| **Psycho-fit** | **15** | **Loaded from `data/psyche_profile.json` — your work-style preferences** |

**Hard gates** that zero a job's score: senior-bureaucrat ladder titles (Sr Manager / Director / VP / Principal), out-of-list locations (configurable via `KILL_LOCATION_HITS`).

## Psyche profile — work-style scoring (v0.2)

Fork `data/psyche_profile.json` to your own preferences. The schema:

- **`dimensions`** — your personality / work-style signals (autonomy_need, bureaucracy_tolerance, builder_orientation, etc.) — informational + read by future iterations.
- **`loved_signals` / `hated_signals`** — case-insensitive substring tokens checked against:
  - `title` — the most direct signal of role shape ("founding", "deployed" vs "vp", "head of")
  - `company_or_note` — company name + its `_note` field from `portals.json` (stage/size flags)
  - `industry` — the moat-tag classification

Edit the file, re-run `jobpilot queue --refresh`, and `jobpilot psyche` to see the scoring shift live. This is the dimension that makes JobPilot prefer **lean, autonomous, builder-flavored roles** over flashy-but-bureaucratic ones, automatically.

## Known limitations (v0.1)

- **macOS only.** The Chrome launcher hardcodes the macOS Chrome path. Linux/Windows support is not yet wired.
- **You must log into LinkedIn manually** in the debug-Chrome window before running `jobpilot start`. No stored credentials.
- **Alpha quality.** LinkedIn's UI changes frequently; selectors may drift. File an issue if something breaks.
- **Human-in-the-loop by design.** JobPilot pauses before every submit. It does not autonomously apply.

## Privacy

All your profile data, application history, and learning state live in `data/` on your own machine. Nothing is uploaded anywhere by JobPilot itself. (Third-party APIs you choose to configure — e.g. Google Gemini for resume tailoring — are subject to their own terms.)

## Directory Structure

```
jobpilot/
├── core/           # CDP bridge, profile store, LinkedIn parsing
├── ui/             # Ghost overlay injection
├── learning/       # Action recording and pattern extraction
├── data/           # Your profile and templates (gitignored)
└── scripts/        # Chrome launcher
```

## Philosophy

**Training wheels first.** JobPilot starts in semi-auto mode:
1. Suggests field values with confidence indicators
2. Waits for your approval before filling
3. Learns from your corrections
4. Gradually increases autonomy as it earns trust

At the final submit step, JobPilot opens a review panel showing your filled answers, fit score, matched skills, resume context, and the latest tailored resume draft — nothing is submitted until you click through. If the draft includes a PDF, JobPilot prefers it for the resume upload field.
