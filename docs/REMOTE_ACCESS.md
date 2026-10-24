# Directing work from your phone

For short instructions, approvals, and quality review, use the ChatGPT mobile
app's **Remote** connection to the Mac running the desktop app. It supports
continuing tasks and reviewing their results using the host's existing tools
and projects. Availability depends on the account and rollout.
See [Remote](https://learn.chatgpt.com/docs/remote).

## One-time connection

1. Update both apps and sign into the same account and workspace.
2. On the Mac, open the desktop app's **Settings > Connections > Control this
   Mac > Set up** (or **Add**), then complete its verification.
3. Scan the displayed QR code with the phone and finish pairing there.
4. Confirm that the Mac appears connected in **Remote** on the phone.

Pairing is performed by the user in the apps. The workflow below does not
enable access itself. No custom public server or Tailscale tunnel is needed
for this native connection. See the
[connection guide](https://learn.chatgpt.com/docs/remote-connections).

## Keep the Mac available

The host must remain awake, online, with the desktop app running. Its connection
settings include a keep-awake option while plugged in. OpenAI's documented Mac
setup uses power with the lid open, or power plus an external display when
closed. Choosing Sleep stops access.

If Amphetamine is already installed, its Closed-Display Mode is an alternative
to test for a plugged-in, closed-lid setup. During an active session, **Allow
system sleep when display is closed** should be off. Installing Amphetamine
alone does not start such a session. On Apple Silicon, the author's
[Power Protect helper](https://github.com/x74353/Amphetamine-Power-Protect)
addresses interruptions when power is connected or disconnected. Its presence
does not prove the current session works; check on the actual Mac.

Before relying on this away from home, start a harmless task from the phone
over cellular with the Mac plugged in and its lid closed. Confirm completion,
open its result, and send a follow-up. If that fails, report the host as offline
and resolve the host condition. This is keeping the Mac available, not a tested
mechanism to wake it from sleep or power-off.

## A short direction-and-review loop

The optional [Mobile Director skill](../skills/mobile-director/SKILL.md) helps
the agent present a small, evidence-backed decision queue across existing
tasks. Install its folder into the local Codex skills directory to use it;
copying instructions is separate from pairing the phone.

Useful requests in a connected task:

- "Use $mobile-director. What needs my judgment?"
- "Show me the actual phone preview and what still needs fixing."
- "Make the opening stronger; show me the revised version."

When the app supports choice controls, the agent can offer a concrete decision
such as **Use this version / Revise it / Later**. These are agent interaction
choices, not custom notification buttons shipped by this repository. Accepting
a version is separate from an explicitly requested send or publication.

Completion and attention notifications come from the native app. This skill
does not add a daily schedule or an always-on cross-task scanner.

## Retire a stale Crib notification

The existing **Crib** shortcut may simply open `Gigpilot_Away/crib_sheet.md`
from iCloud in Quick Look. That reads a file; it does not start Mac work.
Disabling sheet generation does not disable a phone's scheduled automation.

On the iPhone, open **Shortcuts > Automation**, find the automation that runs
**Crib**, and disable its automatic execution. Personal automations are specific
to their device and do not sync to the Mac. See Apple's
[personal automation guide](https://support.apple.com/en-gb/guide/shortcuts/apd690170742/9.0/ios/26).

After Remote is paired, a Home Screen entry into Remote can be a convenient
launch point. Verify the Shortcuts actions actually offered by the installed
iPhone app before replacing Crib; do not assume an undocumented action or URL
can submit a task.

## Optional terminal access over Tailscale

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
6. **Keep the Mac awake/on** while you're away using a verified configuration
   as described above. An idle-sleep assertion from `caffeinate` alone is not
   proof that a closed-lid session remains reachable.

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
