# The Gigs Lane

JobPilot's main commands help you apply for full-time jobs. The **gigs lane** is a second track for freelance and contract work: a local radar that scans public gig sources, scores what it finds against your profile, writes a daily digest, and (optionally) pushes a short summary to your phone.

It does **not** auto-submit proposals or drive marketplace accounts. The system finds and drafts; you approve and send.

## What it does

```text
public sources -> score/filter -> digest + proposal draft -> push notification
```

Current sources:

- RemoteOK
- WeWorkRemotely
- Himalayas
- Hacker News "Who's Hiring"
- Saved Upwork PDF exports, only when explicitly enabled (see below)

## Commands

All gigs commands live under `jobpilot gigs`:

```bash
./jobpilot gigs scan        # show top gigs from all sources (dry run, sends nothing)
./jobpilot gigs digest      # scan + filter + dedupe + write digest + push
./jobpilot gigs stats       # dedupe + pipeline + health summary
./jobpilot gigs health      # source scrape health + last digest heartbeat
./jobpilot gigs feedback    # aggregate your pass reasons from the pipeline
./jobpilot gigs weekly-summary   # one-screen week in review
```

Try it manually first:

```bash
./jobpilot gigs scan --top 10 --min-score 60
./jobpilot gigs digest --top 12 --min-score 60
```

## Your profile

On first run the gigs lane writes `data/gigs/preferences.json` with neutral placeholder values (name, pay targets, skill keywords, background bullets). Edit that file to make scoring and drafts yours. It is gitignored — your personal details never leave your machine.

## Where things land

- **Digests** — `Gigpilot_Digests/` in your iCloud Drive (so you can read them on your phone).
- **Pipeline** — `GigPilot/pipeline.md` in iCloud Drive: one table row per gig, with a Status column you edit.
- **Crib sheet** — `Gigpilot_Away/crib_sheet.md` in iCloud Drive: refreshed each digest with standard ATS form answers for copy/paste while applying from your phone.
- **State** — `data/gigs/` inside the repo (gitignored): `seen.json`, `sources_health.json`, `last_run.json`, `feedback.jsonl`, `preferences.json`.
- **Logs** — `~/Library/Logs/jobpilot-gigs/`.

## The phone workflow

Open `GigPilot/pipeline.md` in the Files app and edit the **Status** column:

- `s` or `save` → saved (creates a Reminder on the next run)
- `p` or `pass` → passed (excluded from future digests)
- `drafted` / `sent` / `replied` / `interview` / `hired` — track outcomes

Empty Status means `new`. The gigs lane re-reads your edits before each save, so changes you make during a scan are not overwritten.

When you pass on a gig, add a reason in **Notes** so the scorer can be tuned over time:

```text
pass:wrong-stack
pass:low-pay
pass:wrong-role
```

`./jobpilot gigs feedback` aggregates those reasons.

## Phone push (ntfy)

Optional. Install the [ntfy](https://ntfy.sh) app on your phone, subscribe to a private topic of your choosing, then store the topic outside the repo:

```bash
mkdir -p "$HOME/.secrets"
printf 'export NTFY_TOPIC="your-private-topic"\n' >> "$HOME/.secrets/api-keys.env"
```

The scheduled scripts source that file automatically. Do not put the topic in tracked files — anyone who knows it can read your pushes.

Each notification surfaces the direct apply target where one can be extracted (a `mailto:` link or an ATS form) as an **Apply** action button, so applying from the phone takes a handful of taps. Resume and cover-letter uploads stay manual by design.

## Running it on a schedule

Installs two macOS LaunchAgents for the logged-in user — a digest at 8am and 5pm, and a weekly summary on Sundays at 9am:

```bash
./scripts/install_gigs_launchd.sh install
./scripts/install_gigs_launchd.sh status
./scripts/install_gigs_launchd.sh uninstall
```

Prefer interval-based runs instead of the fixed times:

```bash
GIGPILOT_INTERVAL_SECONDS=3600 ./scripts/install_gigs_launchd.sh install
```

Logs from scheduled runs go to `~/Library/Logs/jobpilot-gigs/`. A failed digest pushes a high-priority alert to your phone (when `NTFY_TOPIC` is set) rather than failing silently.

## Upwork (opt-in, no login)

Upwork is off by default. The gigs lane never logs in to Upwork or scrapes behind an account. If you want Upwork leads in the mix:

1. Manually save promising Upwork posts as PDFs into a folder of your choosing.
2. Point the gigs lane at it and enable the source:

   ```bash
   export GIGPILOT_UPWORK_LEADS_DIR="$HOME/Documents/upwork-leads"
   GIGPILOT_INCLUDE_UPWORK=1 ./jobpilot gigs scan --top 10 --min-score 60
   ```

3. Review the generated draft in the digest and send manually if it makes sense.

## Environment variables

| Variable | What it does | Default |
|---|---|---|
| `GIGPILOT_DATA_DIR` | Repo-local state dir | `data/gigs/` in the repo |
| `GIGPILOT_ICLOUD_ROOT` | iCloud Drive root for pipeline/digests | `~/Library/Mobile Documents/com~apple~CloudDocs` |
| `GIGPILOT_PIPELINE_DIR` | Where `pipeline.md` lives | `GigPilot/` under the iCloud root |
| `GIGPILOT_DIGESTS_DIR` | Where digests are written | `Gigpilot_Digests/` under the iCloud root |
| `GIGPILOT_AWAY_DIR` | Where the crib sheet is written | `Gigpilot_Away/` under the iCloud root |
| `NTFY_TOPIC` | Private ntfy topic for phone pushes | unset (pushes skipped) |
| `GIGPILOT_INCLUDE_UPWORK` | Set to `1` to ingest saved Upwork PDFs | off |
| `GIGPILOT_UPWORK_LEADS_DIR` | Folder of saved Upwork PDF exports | `upwork-leads/` under the data dir |
| `GIGPILOT_PYTHON` | Interpreter used by the scheduled scripts | the repo's `.venv` |
| `GIGPILOT_INTERVAL_SECONDS` | Interval mode for the launchd schedule | unset (8am/5pm calendar mode) |

## Safety boundary

Same spirit as the rest of JobPilot:

- No auto-submit.
- No stored marketplace credentials.
- No scraping behind logged-in walls.
- The final send/apply decision stays manual.

That boundary keeps this useful without turning it into a brittle account-risk bot.

## Migrating from standalone GigPilot

If you previously ran GigPilot as its own repo with its own launchd agents, see [GIGS_MIGRATION.md](GIGS_MIGRATION.md) for the cutover runbook.
