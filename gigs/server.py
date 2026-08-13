"""Mobile swipe server — a phone-first, on-demand job swiper.

Run `jobpilot gigs swipe --host <tailscale-ip>`; open the printed Tailscale URL
on your phone. Tap
"Get jobs" to scan, then swipe: right/Apply opens the prefilled email (or apply
page) so you can review and send; opening is logged as drafted, never sent.
Left/Pass logs the pass. One
card at a time. The scan/score/geo/currency engine is shared with the
digest — this is just the interactive front end.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from jobpilot.gigs.core import swipe
from jobpilot.gigs.core.logger import get_logger
from jobpilot.gigs.core.models import Gig

log = get_logger(__name__)
app = FastAPI(title="GigPilot Swipe", version="1.0.0")
# Direct ASGI imports start locked. ``configure_server_access`` is the only
# path that can opt into loopback access or authenticated Tailscale access.
app.state.remote_auth_required = True
app.state.remote_token = None
app.state.allowed_hosts = None

REMOTE_TOKEN_ENV = "JOBPILOT_REMOTE_TOKEN"
REMOTE_TOKEN_HEADER = "X-JobPilot-Token"
_TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9._~-]{32,}")

_PAGE = Path(__file__).parent / "swipe.html"

# In-memory queue for the current session: full Gigs (to record decisions) +
# the card payloads the phone renders. _SCAN_LOCK serializes the ~15s scan so
# a double pull-to-refresh can't mutate _GIGS mid-iteration.
_GIGS: dict[str, Gig] = {}
_SCAN_LOCK = threading.Lock()


def configure_server_access(host: str, token: str | None = None) -> bool:
    """Enable auth for a specific Tailscale bind; reject every other remote bind."""
    # Invalid and interrupted configuration attempts remain fail-closed.
    app.state.remote_auth_required = True
    app.state.remote_token = None
    app.state.allowed_hosts = None
    normalized = host.strip().lower()
    if normalized == "localhost":
        remote = False
    else:
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError as exc:
            raise ValueError(
                "Bind host must be loopback or a specific Tailscale IPv4 address."
            ) from exc
        if address.is_unspecified:
            raise ValueError("Wildcard bind addresses are not allowed.")
        remote = not address.is_loopback
        if remote and not (address.version == 4 and address in _TAILSCALE_V4):
            raise ValueError(
                "LAN/public binds are not allowed; use a specific Tailscale IPv4 address."
            )

    configured_token = token if token is not None else os.environ.get(REMOTE_TOKEN_ENV, "")
    if remote and not _SAFE_TOKEN.fullmatch(configured_token):
        raise ValueError(
            f"Authenticated remote serving requires {REMOTE_TOKEN_ENV} with at least "
            "32 URL-safe random characters."
        )
    app.state.remote_auth_required = remote
    app.state.remote_token = configured_token if remote else None
    app.state.allowed_hosts = (
        {normalized}
        if remote
        else {"127.0.0.1", "localhost", "::1"}
    )
    return remote


def _same_origin_request(request: Request) -> bool:
    """Reject DNS rebinding and cross-site browser writes to the local API."""
    allowed = app.state.allowed_hosts
    if allowed is None:
        return True
    hostname = (request.url.hostname or "").lower()
    client_host = request.client.host if request.client else ""
    is_test_client = hostname == "testserver" and client_host == "testclient"
    if not is_test_client and hostname not in allowed:
        return False
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return False
    origin = request.headers.get("Origin", "")
    if not origin:
        return True
    parsed = urlsplit(origin)
    request_port = request.url.port or (443 if request.url.scheme == "https" else 80)
    origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return (
        parsed.scheme == request.url.scheme
        and (parsed.hostname or "").lower() == hostname
        and origin_port == request_port
    )


@app.middleware("http")
async def require_remote_auth(request: Request, call_next):
    """Authenticate HTML and API requests whenever the server is non-loopback."""
    if not _same_origin_request(request):
        return JSONResponse(
            {"detail": "Untrusted request origin or host."},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    if not app.state.remote_auth_required:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
    expected = app.state.remote_token or ""
    supplied = request.headers.get(REMOTE_TOKEN_HEADER, "")
    authorization = request.headers.get("Authorization", "")
    if not supplied and authorization.startswith("Bearer "):
        supplied = authorization.removeprefix("Bearer ")
    if not supplied:
        supplied = request.query_params.get("token", "")
    if not supplied or not hmac.compare_digest(supplied, expected):
        return JSONResponse(
            {"detail": "Remote authentication required."},
            status_code=401,
            headers={
                "Cache-Control": "no-store",
                "WWW-Authenticate": "Bearer",
            },
        )
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    return HTMLResponse(_PAGE.read_text())


@app.get("/api/meta")
def meta() -> JSONResponse:
    """Lightweight criteria + resume map for the phone start screen (no scan)."""
    return JSONResponse(swipe.session_meta())


@app.get("/api/queue")
def queue() -> JSONResponse:
    """Return the current in-memory swipe queue without side effects."""
    snapshot = list(_GIGS.values())
    return JSONResponse({
        "cards": [swipe.card(g) for g in snapshot],
        "count": len(snapshot),
        "meta": swipe.session_meta(),
    })


@app.post("/api/queue/refresh")
def refresh_queue() -> JSONResponse:
    """Explicitly scan and replace the swipe queue. Shows every
    undecided role (not just digest-leftovers) — fresh_only=False so the
    digest's best finds, sitting in pipeline.md as `new`, surface here too."""
    try:
        with _SCAN_LOCK:
            gigs = swipe.build_queue(fresh_only=False)
            _GIGS.clear()
            _GIGS.update({g.id: g for g in gigs})
            snapshot = list(_GIGS.values())
    except Exception:
        log.exception("queue scan failed")
        return JSONResponse(
            {"cards": [], "count": 0, "error": "Scan failed — is the Mac online?"},
            status_code=503,
        )
    return JSONResponse({
        "cards": [swipe.card(g) for g in snapshot],
        "count": len(snapshot),
        "meta": swipe.session_meta(),
    })


class Decision(BaseModel):
    id: str
    action: Literal["apply", "pass"]
    reason: str = ""


@app.post("/api/decision")
def decide(d: Decision) -> JSONResponse:
    gig = _GIGS.get(d.id)
    if gig is None:
        return JSONResponse(
            {"ok": False, "error": "unknown gig (queue may have refreshed)"},
            status_code=404,
        )
    try:
        status = swipe.record_decision(gig, d.action, d.reason)
    except Exception as exc:  # write refused / pipeline error — don't fake success
        log.error("decision not recorded for %s: %s", d.id, exc)
        return JSONResponse({"ok": False, "error": "Couldn't save — try again"}, status_code=503)
    # Keep the gig in _GIGS so an Undo can revert it; the phone owns the deck.
    return JSONResponse({"ok": True, "status": status})


class UndoReq(BaseModel):
    id: str


@app.post("/api/undo")
def undo(u: UndoReq) -> JSONResponse:
    gig = _GIGS.get(u.id)
    if gig is None:
        return JSONResponse({"ok": False, "error": "unknown gig"}, status_code=404)
    try:
        swipe.undo_decision(gig)
    except Exception as exc:
        log.error("undo failed for %s: %s", u.id, exc)
        return JSONResponse({"ok": False, "error": "undo failed"}, status_code=503)
    return JSONResponse({"ok": True})


def _tailscale_ip() -> str | None:
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5,
        )
        ip = out.stdout.strip().splitlines()
        return ip[0] if ip else None
    except Exception:
        return None


def _print_qr(url: str) -> None:
    """Print a scannable terminal QR for the phone URL (no-op if qrcode absent)."""
    try:
        import qrcode
    except ImportError:
        return
    q = qrcode.QRCode(border=1)
    q.add_data(url)
    q.make()
    print("  Scan with your phone camera:")
    q.print_ascii(invert=True)
    print()


def run_server(host: str = "127.0.0.1", port: int = 8799) -> None:
    """Serve the swiper, defaulting to loopback for local-only access."""
    import uvicorn

    remote = configure_server_access(host)
    ts = _tailscale_ip()
    phone_url = f"http://{ts}:{port}/" if ts and host == ts else ""
    print("\n  GigPilot Swipe — open on your phone:")
    if phone_url:
        print(f"    {phone_url}   (authenticated Tailscale)")
        print("    Append ?token=$JOBPILOT_REMOTE_TOKEN on first open.")
    print(f"    http://localhost:{port}/   (this Mac)\n")
    if phone_url:
        token = os.environ[REMOTE_TOKEN_ENV]
        _print_qr(f"{phone_url}?token={token}")
    # Remote access logs stay off so navigation query tokens cannot be logged.
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=not remote)
