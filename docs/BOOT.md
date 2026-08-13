# JobPilot — get it running

## One command

```bash
cd ~/AI_Workspace/projects/jobpilot
./scripts/boot.sh
```

## What boot starts

| Service | Port | Purpose |
|---------|------|---------|
| Chrome (CDP) | 9222 | Optional read-only active-job context |
| Dashboard | **8767** | Queue and applications, local-only by default |
| Swipe | **8799** | Job swiper, local-only by default |
| EYE backend | 8766 | Code visualizer — do not use for JobPilot |

Both personal-data APIs bind to `127.0.0.1` unless remote access is explicitly
enabled. For deliberate phone access over Tailscale:

```bash
export JOBPILOT_REMOTE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
JOBPILOT_REMOTE_ACCESS=tailscale ./scripts/boot.sh
```

Boot prints a warning whenever this mode is active. Remote mode binds only to
the Mac's specific Tailscale IPv4 address and requires the configured token for
every HTML and API request. Wildcard, LAN, and public binds are refused. Only
the exact access values `local` (the default) and `tailscale` are accepted.

Open a remote page by appending `?token=` and the value of
`JOBPILOT_REMOTE_TOKEN` to the printed dashboard or swipe URL. The page then
sends that token in an authentication header for its API calls. Remote access
logs are disabled so query tokens are not written to server logs. Treat the
token like a password: do not paste it into chat, screenshots, or log files.
An installed swipe LaunchAgent starts on loopback at login; the command above
passes the token and deliberately restarts it in authenticated Tailscale mode
for that login session.

Each boot performs a controlled restart of dashboard and non-LaunchAgent swipe
processes recorded by boot, so host, port, mode, and token changes take effect
immediately. If a saved PID belongs to any other live command, boot refuses to
kill it and stops with an error instead of claiming JobPilot is ready.

## Daily workflow

```bash
./scripts/boot.sh              # Chrome + dashboard
jobpilot queue --refresh       # rebuild the verified company slate
jobpilot doctor --no-bro       # verify health
./scripts/stop.sh                # stop dashboard only
```

## iTerm (recommended terminal)

```bash
jobpilot iterm --install   # once
source ~/.zshrc
jp                         # one window: boot + HUD + logs (reuses if open)
```

`jp` with arguments still runs the CLI: `jp queue --refresh`, `jp hud --pick`, etc.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Chrome CDP fail | `./scripts/launch_chrome.sh` |
| Port 8767 in use | `./scripts/stop.sh` then boot again |
| Wrong service on 8767 | EYE owns **8766** only — JobPilot must use **8767** |
| `jobpilot: command not found` | `source .venv/bin/activate` or re-run boot.sh |
| Doctor WARN `skipped` status | cosmetic DB quirk — does not block apply flow |
| Remote server refuses to start | Set a URL-safe `JOBPILOT_REMOTE_TOKEN` of at least 32 random characters |
| Remote page says authentication required | Reopen it with `?token=$JOBPILOT_REMOTE_TOKEN` appended |

## First-time setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
jobpilot profile --edit
```
