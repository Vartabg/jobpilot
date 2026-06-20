# Driving your agents from your phone (terminal over Tailscale)

Run Claude Code (and anything else) on this Mac, and reach it from your phone
with a terminal app like [Blink Shell](https://blink.sh). The work runs in
`tmux` **on the Mac**, so the phone is just a disposable viewport: lose the
connection, switch apps, change networks — your session keeps running and you
reattach right where you left off.

```
Blink (phone) ──mosh over Tailscale──> Mac ──> tmux "agents" session ──> claude
```

## One-time setup on the Mac

1. **Enable Remote Login (SSH):** System Settings → General → Sharing →
   Remote Login → on (allow your user). Or: `sudo systemsetup -setremotelogin on`.
2. **Install mosh** (resilient mobile shell — survives roaming and sleep):
   `brew install mosh`.
3. **Put Homebrew on the PATH for SSH command sessions.** SSH runs commands in
   a bare shell that reads only `~/.zshenv` (not `.zprofile`/`.zshrc`), so
   `mosh-server` won't be found without this. Add to `~/.zshenv`:
   ```sh
   export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
   ```
4. **Phone-friendly tmux** (optional) — `~/.tmux.conf`:
   ```
   set -g mouse on            # tap/scroll on the touchscreen
   set -g history-limit 50000
   ```
5. **Install the `agents` launcher** onto your PATH:
   `ln -sf "$PWD/scripts/agents" ~/.local/bin/agents`.
6. **Keep the Mac awake/on** while you're away — e.g. Amphetamine (allows
   closed-clamshell), or `caffeinate`. A powered-off/asleep Mac is unreachable.

## Reaching it (private, from anywhere)

Use [Tailscale](https://tailscale.com): install it on the Mac and the phone
(same account). The Mac is then reachable at its MagicDNS name
(`<your-mac>.<your-tailnet>.ts.net`) from anywhere — no ports opened to the
public internet.

## On the phone (Blink)

1. Make sure Tailscale is on. Connect:
   ```
   mosh <your-user>@<your-mac>.<your-tailnet>.ts.net
   ```
   (Tip: save it as a Host in Blink so it's one tap. If mosh connects then
   freezes, fall back to plain `ssh` — it always works over Tailscale.)
2. Enter the persistent session and start your agent:
   ```
   agents                 # attach-or-create the tmux session
   cd ~/path/to/project
   claude
   ```
3. **Leave:** detach with `Ctrl-b` then `d` (or just background the app).
   **Return:** reconnect → `agents` → exactly where you left off, with any
   output produced while you were gone.

### Phone ergonomics
A terminal on a phone is cramped; mitigate with Blink's **smart-keys bar**
(Esc, Ctrl, Tab, arrows — needed for Claude's keyboard UI), a larger font,
landscape, and `Ctrl-b z` to zoom a tmux pane full-screen. A small Bluetooth
keyboard is a big upgrade for real sessions.

### Security
Prefer key auth (add your Blink public key to `~/.ssh/authorized_keys`) over a
password. Over Tailscale the service is private to your tailnet; SSH is also
reachable on your LAN once Remote Login is on.
