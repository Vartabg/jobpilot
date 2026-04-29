# Setting Up JobPilot for a Friend

Someone you care about asked you to help them get JobPilot running. This takes about 15 minutes and you only need to do it once. After that, they run it themselves.

---

## What you need

- Their Mac (macOS 12 or later — you can check under  → About This Mac)
- About 15 minutes
- The ability to open Terminal

---

## Step 1 — Open Terminal on their Mac

Press `⌘ Space`, type **Terminal**, hit Enter. A black or white window with a prompt will appear. That's where you'll paste commands.

---

## Step 2 — Run the installer

Paste this single line into Terminal and press Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/Vartabg/jobpilot/main/install.sh | bash
```

The script will:
- Check that Python 3.11+ is installed (and tell you how to get it if not)
- Download JobPilot to their home folder
- Install everything needed
- Add a shortcut to their shell

If anything goes wrong, the script will print a clear message about what to fix. Most common issue: Python not installed. If that happens, go to [python.org/downloads](https://www.python.org/downloads/), download Python 3.12, install it, then run the command above again.

---

## Step 3 — Get a free AI API key (for resume tailoring)

The resume tailoring feature needs an AI key. Google Gemini is free:

1. Go to [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. Sign in with a Google account
3. Click "Create API key"
4. Copy the key

Then paste this in Terminal, replacing `YOUR_KEY_HERE` with the real key:

```bash
echo 'export GEMINI_API_KEY=YOUR_KEY_HERE' >> ~/.zshrc && source ~/.zshrc
```

---

## Step 4 — Set up their profile

```bash
source ~/jobpilot/.venv/bin/activate
jobpilot profile --edit
```

Walk them through the prompts:
- **First name / Last name** — their name
- **Email** — the email they use for job applications
- **Phone** — their phone number
- **Years of experience** — rough number is fine
- **Resume path** — the full path to their resume PDF (e.g. `/Users/theirname/Desktop/resume.pdf`)

Tip: to find the path to a file, drag it into the Terminal window — it will paste the full path.

---

## Step 5 — Run a health check

```bash
jobpilot doctor
```

This checks that everything is wired up correctly. Green checkmarks = good.

---

## Step 6 — Show them the three commands they'll use every day

```bash
# Find open roles at a specific company
jobpilot scan --greenhouse [company name] -k "[job title keyword]"

# Score a job description against their profile
jobpilot score ~/Downloads/job-description.txt

# Generate a tailored resume for a specific role
jobpilot resume ~/Downloads/job-description.txt --pdf
```

Tell them: each time they open a new Terminal window, they need to run this first:
```bash
source ~/jobpilot/.venv/bin/activate
```

Or they can type `jobpilot-start` (the installer added this shortcut for them).

---

## That's it

You've given someone a real tool for their job search. Thank you for taking the time.

If anything breaks or a command stops working, they can open an issue at:
[github.com/Vartabg/jobpilot/issues](https://github.com/Vartabg/jobpilot/issues)
