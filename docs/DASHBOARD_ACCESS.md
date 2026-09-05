# Dashboard access and recovery

Start the dashboard with `jobpilot serve`. For phone access, use
`jobpilot serve --host <your-tailscale-ip> --allow-lan`. The gigs command remains
`jobpilot gigs swipe --host <your-tailscale-ip>`.

Open the private link printed in the terminal, or scan the gigs QR code.
It signs in this browser and immediately removes the token from the address
bar. The session supports both API calls and the dashboard's Fill Button link.
Use the same hostname for both servers to share the session. If a browser
session expires, open the private link again. Never share this access link.

The servers share `data/server_token`, created with owner-only permissions.
`JOBPILOT_SERVER_TOKEN` overrides it. Rotating the configured token invalidates
existing sessions. HTML never embeds the access token. Browser sessions use
HttpOnly, SameSite=Strict cookies; API calls also require a custom request
header. HTTPS sessions additionally use Secure cookies. CLI servers disable
request access logs so sign-in URLs are not written there. Keep any proxy's
logs from recording those URLs as well.

Programmatic clients can send `X-JobPilot-Token` with the configured token.
A query token only establishes a browser session at `/` or `/install`.

## Applying from the phone

For web forms, Apply reserves a tab during your tap and opens the form after
saving your choice. If saving fails, that tab closes and the card stays.
If your browser blocks the tab, allow pop-ups for JobPilot and retry; no
application decision is recorded by that blocked attempt.

For email leads, saving displays a persistent **Open email draft** link.
Tap it to open Mail, review, attach your resume, and send yourself. A tab
closed during saving likewise leaves an **Open application** link. Undo only
changes the displayed queue after its request succeeds.

## Recovering a damaged queue

A malformed `data/queue.json` stays in place. JobPilot reports an error on
every read until it is repaired; refreshing cannot silently replace history.
Each distinct damaged version gets a `queue.json.<content-hash>.corrupt`
copy. Repeated reads of the same content reuse that recovery copy.

Stop the dashboard, inspect those copies, and repair or restore `queue.json`
from a known-good copy. Keep the recovery copies until you have checked your
applied and skipped statuses. The dashboard returns an actionable 503 while
recovery is required.

## Verification

Run the Python suite and JavaScript interaction tests:

```sh
PYTHONPATH="$(dirname "$PWD")" python -m pytest tests/ -q
node --test tests/swipe_actions.test.cjs
```

The CI workflow also runs `python scripts/check_ui.py --axe <axe.min.js>`
against synthetic loopback servers. It fails on browser interaction errors,
AA accessibility violations, missing landmarks, or missing audit dependencies.
On this Mac, supply `--executable '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'`.
