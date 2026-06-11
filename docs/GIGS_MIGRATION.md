# Migrating from standalone GigPilot

Runbook for cutting over from a standalone GigPilot checkout to the gigs lane inside this repo. One-time, ~10 minutes, fully reversible until step 5.

This is a **document, not a script** — run each step yourself and check the result before moving on. Nothing below is executed automatically.

Throughout, `OLD` is the standalone GigPilot checkout and `NEW` is this repo. Adjust to your paths:

```bash
OLD=/Users/vartny/AI_Workspace/projects/gigpilot
NEW=/Users/vartny/AI_Workspace/projects/jobpilot
```

## What moves, what stays

| Thing | Action |
|---|---|
| launchd agents (`com.vartny.gigpilot.*`) | Uninstall; replaced by `com.vartny.jobpilot.gigs.*` |
| State files (`data/*.json`, `data/feedback.jsonl`, `data/preferences.json`) | Copy to `NEW/data/gigs/` |
| iCloud pipeline (`GigPilot/pipeline.md`) | **Stays put** — both versions read the same path |
| iCloud digests (`Gigpilot_Digests/`) | **Stays put** — same |
| iCloud crib sheet (`Gigpilot_Away/`) | **Stays put** — same |
| `~/.secrets/api-keys.env` (NTFY_TOPIC etc.) | **Stays put** — the new scripts source the same file |
| Old logs (`~/Library/Logs/gigpilot/`) | Leave for reference; new logs go to `~/Library/Logs/jobpilot-gigs/` |
| Old checkout | Keep until you've seen a few clean scheduled runs, then archive |

The iCloud paths are shared because both old and new code resolve them the same way (`GIGPILOT_ICLOUD_ROOT` and friends). Your pipeline history, statuses, and pass-reason notes carry over with zero copying.

## 1. Stop the old launchd agents

```bash
cd "$OLD" && ./scripts/install_launchd.sh uninstall
```

Verify nothing is left loaded:

```bash
launchctl list | grep gigpilot   # expect no output
```

Do this first — two digest agents writing the same iCloud pipeline at 8am is the failure mode this runbook exists to prevent.

## 2. Copy state

The seen-gig memory, source-health history, last-run heartbeat, feedback log, and your preferences:

```bash
mkdir -p "$NEW/data/gigs"
cp "$OLD"/data/*.json "$NEW/data/gigs/"
cp "$OLD"/data/feedback.jsonl "$NEW/data/gigs/" 2>/dev/null || true
```

`data/` is gitignored in this repo, so nothing personal can leak into a commit. Skipping this step doesn't break anything, but the first new digest would re-surface every gig the old install had already shown you.

## 3. Install the new agents

```bash
cd "$NEW" && ./scripts/install_gigs_launchd.sh install
```

Same cadence as before: digest at 8am and 5pm, weekly summary Sundays at 9am. The labels are `com.vartny.jobpilot.gigs.digest` and `com.vartny.jobpilot.gigs.weekly`, so they cannot collide with the old ones.

## 4. Verify with one supervised digest

Don't wait for 8am — run the exact script launchd will run, watch the log, and check the output landed:

```bash
"$NEW/scripts/gigs_digest.sh"; echo "exit: $?"
tail -40 ~/Library/Logs/jobpilot-gigs/digest-$(date +%Y%m%d).log
```

Check:

- exit 0 and a normal run log (sources fetched, digest written);
- a fresh digest file in `Gigpilot_Digests/` in iCloud;
- `pipeline.md` still has your existing rows and statuses (the new code read the same file the old one wrote);
- a push arrived on the phone, if `NTFY_TOPIC` is set;
- `cd "$NEW" && ./jobpilot gigs health` shows a recent heartbeat.

Also confirm the agents are loaded for the scheduled runs:

```bash
"$NEW/scripts/install_gigs_launchd.sh" status
```

## 5. Retire the old checkout (later)

After a few days of clean scheduled runs, archive or delete the old checkout. If anything goes wrong before then, rollback is the reverse: `./scripts/install_gigs_launchd.sh uninstall` here, `./scripts/install_launchd.sh install` there — the shared iCloud files mean either side picks up where the other left off.
