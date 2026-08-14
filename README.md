# JobPilot

**Built by a Navy vet who was in your shoes. Free. Open source. Yours.**

---

## What this does for you

Applying for jobs is exhausting. You spend hours reading postings, rewriting your resume for each one, filling out the same fields over and over — and most applications disappear into silence.

JobPilot handles the repetitive parts so you can focus on the conversations that matter.

**Here's what it actually does:**

| What you want | What JobPilot does |
|---|---|
| Find open jobs at specific companies | Scans company hiring portals (Greenhouse, Lever, Ashby) for roles that match your background |
| Know if a job is worth applying to | Verifies the live posting, grades its source evidence, and links each recommendation to your profile or a true life account |
| Stop rewriting your resume from scratch | Generates a tailored resume draft for each role in minutes |
| Not miss important fields on applications | Produces a labeled paste sheet for you to review and paste by hand |

**What it won't do:** Fill or submit a live ATS form. It prepares verified drafts and a paste sheet; you paste, review, and submit in your normal browser.

---

## Who this is for

You don't need to be a programmer to use JobPilot. You need:

- A Mac (macOS 12 or later)
- A PDF of your resume
- 20 minutes to get set up

If that's you, keep reading. If you've never opened Terminal before, jump to the [**"Ask a friend to set this up"**](#asking-a-friend-to-help) section — it's a 15-minute favor anyone with basic tech skills can do for you.

---

## Setup

### Option 1 — One command (if you're comfortable with Terminal)

Open Terminal (press `⌘ Space`, type "Terminal", hit Enter) and paste this:

```bash
curl -fsSL https://raw.githubusercontent.com/Vartabg/jobpilot/main/install.sh | bash
```

That's it. The script checks your system, installs what's needed, and tells you what to do next.

### Option 2 — Manual install (if you prefer step-by-step)

```bash
# 1. Get the code
git clone https://github.com/Vartabg/jobpilot.git
cd jobpilot

# 2. Set up a clean Python environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install JobPilot
pip install -e .
```

---

## Your first 10 minutes

**Step 1 — Set up your profile** (do this once)

```bash
jobpilot profile --edit
```

It will ask for contact details, experience, skills, target role titles, and the path to your resume PDF. These explicit facts—and optional true life accounts—are the evidence JobPilot uses; it does not infer a personality from job-title keywords.

**Step 2 — Find jobs at companies you care about**

```bash
# Use the matching flag for the company's ATS: --greenhouse, --lever, or --ashby
jobpilot scan --greenhouse amazon -k "operations manager"
```

**Step 3 — Assess a job before you spend time applying**

```bash
# Paste a job description or save it in a text file
jobpilot score ~/Downloads/job-description.txt
```

You'll see a qualification lower bound, work-context evidence, coverage, and the biggest unknown or gap. Unknown evidence stays unknown; it does not receive neutral points.
This is a role-fit assessment, not permission to apply: only the refreshed queue
can establish current posting, history, and ledger readiness.

**Step 4 — Build a company slate**

```bash
jobpilot queue --refresh
```

JobPilot fetches full descriptions from supported ATS boards, verifies that each role is current, assigns a posting-evidence grade and action state, and keeps one best active role per company. This verifies the listing—not the employer's entire corporate identity—so you still investigate the company before sharing sensitive information. Other roles remain in history as labeled alternatives. Only a current direct-source role with an assessed `apply_now` decision is shown by `jobpilot queue --fresh --json`.

If you configured an external Gmail application-history cache and it becomes stale, search still completes but every positive recommendation is restricted to `Investigate`. JobPilot does not connect to Gmail itself. Generate a fresh full-history JSON export with your read-only Gmail tool, then validate and import it without changing the mailbox:

```bash
jobpilot gmail-sync ~/Downloads/gmail-applications.json
jobpilot queue --refresh
```

The export is deliberately narrow: each record may contain `company`, `title`,
`status`, an HTTPS `url`, one ISO date/timestamp field (`occurred_at`, `date`,
`applied_at`, or `received_at`), and an optional opaque `message_id`. Subjects,
message bodies, snippets, attachments, and arbitrary extra fields are rejected.

For the full control-center dashboard, run `jobpilot serve` and open `http://127.0.0.1:8767/`. The dashboard shows apply readiness, relocation-supported leads, current direct-role coverage, source health, the 30-day application funnel, explicit-outcome sample size, and every role's evidence card. Its Relocation view sorts confirmed support before conditional support and shows the exact posting statement plus destination; “must relocate” never counts as company support. The dashboard depends on the local API server, so opening the HTML file directly is intentionally unsupported.

**For AI agents:** use `jobpilot agent capabilities` to discover the versioned, model-neutral contract. `jobpilot agent opportunities`, `assess`, `preflight`, `dashboard`, and `outcome` expose the same service through JSON; versioned HTTP and Python access are also available. Agents can search, assess, prepare evidence, and record only human-confirmed outcomes, but they cannot fill or submit a live ATS form. See [docs/AGENT_API.md](docs/AGENT_API.md).

**Step 5 — Get a tailored resume draft**

```bash
jobpilot resume ~/Downloads/job-description.txt --pdf
```

Generates a resume customized to that specific role. Review it, adjust anything that feels off, then use it.

Resume drafts are deterministic and use only explicit profile and resume evidence; no AI key is required.

---

## Asking a friend to help

If someone is setting this up for you, send them the [**Setup Guide for Helpers**](SETUP_FOR_HELPERS.md). It walks through the full install in plain steps — takes about 15 minutes. Once they're done, you just use the four commands above.

---

## Core commands

```
jobpilot profile          Set up or update your profile (name, resume, experience)
jobpilot scan             Find open roles at companies using Greenhouse, Lever, or Ashby
jobpilot score            Score a job description against your profile
jobpilot resume           Generate a tailored resume for a specific role
jobpilot start            Explain the retired live-form automation flow
jobpilot doctor           Check that everything is working correctly
jobpilot history          See your application history
jobpilot stats            See your application stats
jobpilot queue            Build a verified, evidence-linked company slate
jobpilot gmail-sync       Validate and import a read-only Gmail history export
jobpilot log              Mark a manual application so it's tracked alongside JobPilot-assisted ones
jobpilot answer           Save and reuse the answers you write for application questions
```

---

## Looking for freelance work too?

JobPilot also has a **gigs lane** — a second track for freelance and contract work. It scans public gig boards twice a day, scores what it finds against your profile, writes a digest you can read on your phone, and keeps a simple pipeline file you update by typing one letter (`s` to save, `p` to pass). Same rule as everything else here: it finds and drafts, you review and send — it never submits anything for you. Setup and the full phone workflow are in the [Gigs Lane guide](docs/GIGS.md).

---

## Honest limitations

- **macOS only right now.** Windows and Linux support is on the roadmap.
- **AI is optional and limited to advisory features you invoke explicitly.** Interview preparation and advice can use a configured Gemini or local Bro backend. Role decisions, resumes, cover-letter drafts, and application answers remain deterministic so model prose cannot become an unsupported external claim.
- **The scan works on public ATS boards.** If a company uses a private hiring system or doesn't use Greenhouse/Lever/Ashby, the scan won't find those roles.
- **Gmail history is an optional external export, not an inbox integration.** JobPilot validates and atomically imports the configured cache; it never signs into or modifies Gmail. Missing or stale fail-closed history restricts recommendations to `Investigate` until refreshed.
- **You paste and submit by hand.** JobPilot never fills or submits the live ATS form.
- **Posting evidence is a grade, not a company-trust probability.** `A/B recommend` requires a current direct ATS listing; `review` needs human verification; `hold` needs a refresh; `block` means closed, conflicting, expired, or scam-pattern evidence. Investigate the employer separately.
- **Work context is observed, not a personality diagnosis.** Customer interaction, autonomy, field work, presentations, ambiguity, and similar signals are shown only when the job description and your evidence support them.
- **Relocation support is evidence, not a location guess.** `offered` requires explicit company support; `conditional` requires explicit may-be-available language; a required move without confirmed support is labeled separately. Remote and home-metro roles are `not_needed`, not relocation offers. Out-of-area roles bypass a narrow personal location gate only when support is explicit or conditional, and relocation never overrides missing qualifications or work authorization.
- **Outcome data never changes ranking weights automatically.** With fewer than 20 roles that have explicit decided outcomes, weights stay fixed. At 20 or more, calibration becomes eligible for explicit manual review only; every change must still be deliberate and tested. Silence remains censored unless you record `no_response`.

---

## Privacy

Your profile, resume, and application history are stored locally. Scans send public job-board requests. If you explicitly invoke an AI-backed interview or advice feature, JobPilot may send the profile fields, job context, and true-account excerpts needed for that request; review that provider's privacy terms first. The optional gigs archive writes drafts to your configured iCloud folder, and optional ntfy pushes send only a short lead summary plus the public source URL—never the personalized draft. Local dashboard APIs bind only to loopback unless you explicitly enable documented authenticated Tailscale access.

---

## Contributing

If you're a developer and you believe in what this is trying to do — making job search tools accessible to people who can't build them — contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT. Free to use, modify, and share.

---

*Built by [Garo Vartabedian](https://github.com/Vartabg). If this helped you land something, I'd love to hear about it.*
