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
| Know if a job is worth applying to | Scores any job posting against your profile — tells you your match % and what's missing |
| Stop rewriting your resume from scratch | Generates a tailored resume draft for each role in minutes |
| Not miss important fields on applications | Suggests answers as you fill out forms, pauses before submit so you stay in control |

**What it won't do:** Apply for you without your review. You always see what's being submitted before anything happens. You're in the loop at every step.

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
playwright install chromium
```

---

## Your first 10 minutes

**Step 1 — Set up your profile** (do this once)

```bash
jobpilot profile --edit
```

It will ask you for your name, email, phone, years of experience, and the path to your resume PDF. Answer the prompts — takes about 2 minutes.

**Step 2 — Find jobs at companies you care about**

```bash
# Replace "amazon" with any company that uses Greenhouse, Lever, or Ashby
jobpilot scan --greenhouse amazon -k "operations manager"
```

**Step 3 — Score a job before you spend time applying**

```bash
# Paste a job description into a text file, or point to a URL
jobpilot score ~/Downloads/job-description.txt
```

You'll see a match score, the skills they're looking for, and what gaps exist. Spend your energy on the roles where you're actually a fit.

**Step 4 — Get a tailored resume draft**

```bash
jobpilot resume ~/Downloads/job-description.txt --pdf
```

Generates a resume customized to that specific role. Review it, adjust anything that feels off, then use it.

---

## Asking a friend to help

If someone is setting this up for you, send them the [**Setup Guide for Helpers**](SETUP_FOR_HELPERS.md). It walks through the full install in plain steps — takes about 15 minutes. Once they're done, you just use the four commands above.

---

## All commands

```
jobpilot profile          Set up or update your profile (name, resume, experience)
jobpilot scan             Find open roles at companies using Greenhouse, Lever, or Ashby
jobpilot score            Score a job description against your profile
jobpilot resume           Generate a tailored resume for a specific role
jobpilot start            Connect to Chrome and get help filling out applications
jobpilot doctor           Check that everything is working correctly
jobpilot history          See your application history
jobpilot stats            See your application stats
jobpilot queue            Build a scored, deduped queue of fresh jobs across multiple companies
jobpilot log              Mark a manual application so it's tracked alongside JobPilot-assisted ones
jobpilot psyche           Show your work-style profile and how it's scoring real jobs
jobpilot answer           Save and reuse the answers you write for application questions
```

---

## Honest limitations

- **macOS only right now.** Windows and Linux support is on the roadmap.
- **Resume tailoring requires an AI API key.** Google Gemini has a free tier — [get one here](https://aistudio.google.com/app/apikey). Set it: `export GEMINI_API_KEY=your_key_here`
- **The scan works on public ATS boards.** If a company uses a private hiring system or doesn't use Greenhouse/Lever/Ashby, the scan won't find those roles.
- **You always review before submit.** JobPilot never applies without showing you what it's about to do.

---

## Privacy

Everything stays on your computer. Your profile, your resume, your application history — none of it is uploaded anywhere by JobPilot. The only network calls are to the AI API you configure (if you use the resume feature) and to the company hiring portals you scan.

---

## Contributing

If you're a developer and you believe in what this is trying to do — making job search tools accessible to people who can't build them — contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT. Free to use, modify, and share.

---

*Built by [Garo Vartabedian](https://github.com/Vartabg). If this helped you land something, I'd love to hear about it.*
