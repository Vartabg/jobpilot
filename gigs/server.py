"""Mobile swipe server — a phone-first, on-demand job swiper.

Run `jobpilot gigs swipe`; open the printed Tailscale URL on your phone. Tap
"Get jobs" to scan, then swipe: right/Apply opens the prefilled email (or apply
page) so you just hit send and logs it as sent; left/Pass logs the pass. One
card at a time. The scan/score/geo/currency engine is shared with the
digest — this is just the interactive front end.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from jobpilot.gigs.core import swipe
from jobpilot.gigs.core.logger import get_logger
from jobpilot.gigs.core.models import Gig

log = get_logger(__name__)
app = FastAPI(title="GigPilot Swipe", version="1.0.0")

_PAGE = Path(__file__).parent / "swipe.html"

# In-memory queue for the current session: full Gigs (to record decisions) +
# the card payloads the phone renders. _SCAN_LOCK serializes the ~15s scan so
# a double pull-to-refresh can't mutate _GIGS mid-iteration.
_GIGS: dict[str, Gig] = {}
_SCAN_LOCK = threading.Lock()


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    return HTMLResponse(_PAGE.read_text())


@app.get("/api/queue")
def queue(refresh: int = 0) -> JSONResponse:
    """Return the swipe queue. Scans on first call (or refresh=1); the scan hits
    the network so the phone shows a loading state meanwhile. Shows every
    undecided role (not just digest-leftovers) — fresh_only=False so the
    digest's best finds, sitting in pipeline.md as `new`, surface here too."""
    try:
        with _SCAN_LOCK:
            if refresh or not _GIGS:
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
    return JSONResponse({"cards": [swipe.card(g) for g in snapshot], "count": len(snapshot)})


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


def run_server(host: str = "0.0.0.0", port: int = 8799) -> None:
    """Serve the swiper. Binds all interfaces by default so the phone can reach
    it over Tailscale; prints the URL (and a scannable QR) to open."""
    import uvicorn

    ts = _tailscale_ip()
    phone_url = f"http://{ts}:{port}/" if ts else ""
    print("\n  GigPilot Swipe — open on your phone:")
    if phone_url:
        print(f"    {phone_url}   (Tailscale — works anywhere)")
    print(f"    http://localhost:{port}/   (this Mac)\n")
    if phone_url:
        _print_qr(phone_url)
    # info level so page visits are logged — needed to confirm the phone is
    # actually reaching the server.
    uvicorn.run(app, host=host, port=port, log_level="info")
