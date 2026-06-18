# JobPilot — get it running

## One command

```bash
cd ~/AI_Workspace/projects/jobpilot
./scripts/boot.sh
```

## What boot starts

| Service | Port | Purpose |
|---------|------|---------|
| Chrome (CDP) | 9222 | LinkedIn / ATS browser control |
| Dashboard | **8767** | Queue, applications, mobile via Tailscale |
| EYE backend | 8766 | Code visualizer — do not use for JobPilot |

## Daily workflow

```bash
./scripts/boot.sh              # Chrome + dashboard
jobpilot start                 # connect and assist with applications
jobpilot doctor --no-bro       # verify health
./scripts/stop.sh                # stop dashboard only
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Chrome CDP fail | `./scripts/launch_chrome.sh` |
| Port 8767 in use | `./scripts/stop.sh` then boot again |
| Wrong service on 8767 | EYE owns **8766** only — JobPilot must use **8767** |
| `jobpilot: command not found` | `source .venv/bin/activate` or re-run boot.sh |
| Doctor WARN `skipped` status | cosmetic DB quirk — does not block apply flow |

## First-time setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
jobpilot profile --edit
```