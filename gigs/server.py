"""Mobile swipe server — a phone-first, on-demand job swiper.

Run `jobpilot gigs swipe`; open the printed Tailscale URL on your phone. Tap
"Get jobs" to scan, then swipe: right/Apply opens the prefilled email (or apply
page) so you just hit send and logs it as sent; left/Pass logs the pass. One
card at a time. The scan/score/Austin-geo/currency engine is shared with the
digest — this is just the interactive front end.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

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
# the card payloads the phone renders.
_GIGS: dict[str, Gig] = {}


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    return HTMLResponse(_PAGE.read_text())


@app.get("/api/queue")
def queue(refresh: int = 0) -> JSONResponse:
    """Return the swipe queue. Scans on first call (or refresh=1); the scan hits
    the network so the phone shows a loading state meanwhile."""
    if refresh or not _GIGS:
        _GIGS.clear()
        gigs = swipe.build_queue()
        for g in gigs:
            _GIGS[g.id] = g
    cards = [swipe.card(_GIGS[g]) for g in _GIGS]
    return JSONResponse({"cards": cards, "count": len(cards)})


class Decision(BaseModel):
    id: str
    action: str  # "apply" | "pass"
    reason: str = ""


@app.post("/api/decision")
def decide(d: Decision) -> JSONResponse:
    gig = _GIGS.get(d.id)
    if gig is None:
        return JSONResponse({"ok": False, "error": "unknown gig id"}, status_code=404)
    status = swipe.record_decision(gig, d.action, d.reason)
    _GIGS.pop(d.id, None)  # don't show it again this session
    return JSONResponse({"ok": True, "status": status, "remaining": len(_GIGS)})


def _tailscale_ip() -> str | None:
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5,
        )
        ip = out.stdout.strip().splitlines()
        return ip[0] if ip else None
    except Exception:
        return None


def run_server(host: str = "0.0.0.0", port: int = 8799) -> None:
    """Serve the swiper. Binds all interfaces by default so the phone can reach
    it over Tailscale; prints the URL to open."""
    import uvicorn

    ts = _tailscale_ip()
    print("\n  GigPilot Swipe — open on your phone:")
    if ts:
        print(f"    http://{ts}:{port}   (Tailscale — works anywhere)")
    print(f"    http://localhost:{port}   (this Mac)\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
