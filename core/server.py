"""
JobPilot HTTP server — phone-first job tracker.

No auto-fill, no Mac Chrome control, no CAPTCHAs to dance around.
Just: curated queue + tap to open + mark applied.

Endpoints:
  GET  /                         → dashboard.html (mobile responsive)
  GET  /api/queue                → current queue as JSON
  GET  /api/profile              → protected local candidate profile (contains PII)
  GET  /install                  → retired live-fill endpoint (410)
  GET  /api/bookmarklet          → retired live-fill endpoint (410)
  POST /api/queue/refresh        → rescan all portals (background)
  POST /api/job/<id>/opened      → mark that you tapped Apply (for analytics)
  POST /api/job/<id>/mark-applied → you submitted it, mark done
  POST /api/job/<id>/skip        → not interested
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
import re
import socket
import subprocess
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from jobpilot.core.application_tracker import get_application_tracker
from jobpilot.core.logger import get_logger
from jobpilot.core.opportunity_ledger import EVENT_TYPES, OpportunityLedger
from jobpilot.core.profile_store import get_profile_store
from jobpilot.core.queue_builder import (
    focus_queue_company_first,
    get_job,
    has_current_action_provenance,
    is_apply_ready,
    load_queue,
    reconcile_queue_with_tracker,
    refresh_queue,
    restrict_queue_for_action_provenance,
    restrict_queue_for_incomplete_history,
    update_job_status,
)

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DASHBOARD_PATH = PROJECT_ROOT / "ui" / "dashboard.html"

app = FastAPI(title="JobPilot Remote", version="0.3.0")
# Importing ``app`` directly (for example, ``uvicorn ...:app``) must not expose
# the dashboard. ``configure_server_access`` is the only path that can opt into
# loopback access or install a valid token for a Tailscale bind.
app.state.remote_auth_required = True
app.state.remote_token = None
app.state.allowed_hosts = None

REMOTE_TOKEN_ENV = "JOBPILOT_REMOTE_TOKEN"
REMOTE_TOKEN_HEADER = "X-JobPilot-Token"
_TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9._~-]{32,}")


def configure_server_access(host: str, token: str | None = None) -> bool:
    """Configure request auth and reject unsafe non-loopback binds.

    Returns ``True`` when the server is in authenticated Tailscale mode.
    """
    # Keep a failed or interrupted configuration attempt closed. A successful
    # loopback configuration below is the sole token-free serving mode.
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
    """Reject DNS rebinding and cross-site browser mutations on local APIs."""
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
    """Require a constant-time token check for every remotely served request."""
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


class ApplicationLogPayload(BaseModel):
    company: str
    title: str = ""
    url: str = ""
    status: str = "applied"
    applied_at: str | None = None
    source: str = "dashboard"
    acquisition_channel: str = "unknown"


def _require_apply_ready(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    if not is_apply_ready(job) or not has_current_action_provenance(job):
        raise HTTPException(
            409,
            detail={
                "message": (
                    "This role cannot be treated as apply-ready because its "
                    "evidence or current source verification is incomplete."
                ),
                "decision": job.decision,
                "legitimacy_state": job.legitimacy_state,
                "next_action": "Refresh the source and review the evidence card.",
            },
        )
    return job


def _record_funnel_event(
    *,
    company: str,
    title: str,
    url: str,
    status: str,
    source: str,
    occurred_at: str = "",
    acquisition_channel: str = "unknown",
) -> None:
    event_type = {
        "started": "discovered",
        "submitted": "applied",
        "abandoned": "withdrawn",
    }.get(status, status)
    if event_type not in EVENT_TYPES:
        return
    ledger = OpportunityLedger()
    try:
        opportunity_id = ledger.upsert_opportunity(
            company,
            title,
            canonical_url=url,
        )
        ledger.append_event(
            opportunity_id,
            event_type,
            occurred_at,
            source,
            {
                "original_status": status,
                "acquisition_channel": acquisition_channel,
            },
        )
    finally:
        ledger.close()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    if not DASHBOARD_PATH.exists():
        raise HTTPException(404, "dashboard.html missing")
    return FileResponse(
        DASHBOARD_PATH,
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/install", include_in_schema=False)
async def install_page() -> None:
    """Explain that live-form automation was retired for the paste flow."""
    raise HTTPException(
        410,
        detail={
            "message": "The live-form fill button has been retired.",
            "next_action": (
                "Use JobPilot to draft a paste sheet, then paste, review, and "
                "submit in your normal browser."
            ),
        },
    )
# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

@app.get("/api/queue")
async def api_queue() -> JSONResponse:
    jobs = load_queue()
    restrict_queue_for_incomplete_history(jobs)
    restrict_queue_for_action_provenance(jobs)
    return JSONResponse([asdict(j) for j in jobs])


@app.post("/api/queue/refresh")
async def api_queue_refresh() -> JSONResponse:
    def _run() -> int:
        return len(refresh_queue(limit=100))

    count = await asyncio.to_thread(_run)
    return JSONResponse({"ok": True, "count": count})


@app.post("/api/queue/reconcile")
async def api_queue_reconcile() -> JSONResponse:
    changed, total = reconcile_queue_with_tracker()
    return JSONResponse({"ok": True, "changed": changed, "total": total})


@app.post("/api/queue/focus")
async def api_queue_focus() -> JSONResponse:
    changed, total, companies = focus_queue_company_first()
    return JSONResponse({
        "ok": True,
        "changed": changed,
        "total": total,
        "companies": companies,
    })


# ---------------------------------------------------------------------------
# Application organizer
# ---------------------------------------------------------------------------


@app.get("/api/applications")
async def api_applications(limit: int = 40) -> JSONResponse:
    tracker = get_application_tracker()
    recent = tracker.get_recent(limit=limit)
    return JSONResponse({
        "stats": tracker.get_stats(),
        "status_counts": tracker.get_status_counts(),
        "recent": [asdict(app) for app in recent],
    })


@app.post("/api/applications/log")
async def api_log_application(payload: ApplicationLogPayload) -> JSONResponse:
    tracker = get_application_tracker()
    try:
        app_row = tracker.log_application(
            company=payload.company,
            title=payload.title,
            url=payload.url,
            status=payload.status,
            applied_at=payload.applied_at,
            source=payload.source or "dashboard",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _record_funnel_event(
        company=app_row.company,
        title=app_row.job_title,
        url=app_row.job_url,
        status=app_row.status,
        source=app_row.source,
        occurred_at=app_row.applied_at,
        acquisition_channel=payload.acquisition_channel,
    )
    changed, total = reconcile_queue_with_tracker()
    return JSONResponse({
        "ok": True,
        "application": asdict(app_row),
        "queue_changed": changed,
        "queue_total": total,
    })


# ---------------------------------------------------------------------------
# Job state
# ---------------------------------------------------------------------------

@app.post("/api/job/{job_id}/opened")
async def api_opened(job_id: str) -> JSONResponse:
    """Track that the user tapped Apply (opened the link). Sets status to
    'viewing' so the UI can show a 'Did you submit?' prompt."""
    _require_apply_ready(job_id)
    if not update_job_status(job_id, "viewing"):
        raise HTTPException(404, f"Job {job_id} not found")
    return JSONResponse({"ok": True})


@app.post("/api/job/{job_id}/mark-applied")
async def api_mark_applied(job_id: str) -> JSONResponse:
    job = _require_apply_ready(job_id)
    if not update_job_status(job_id, "applied"):
        raise HTTPException(404, f"Job {job_id} not found")
    get_application_tracker().mark_applied(job.url, job.title, job.company)
    _record_funnel_event(
        company=job.company,
        title=job.title,
        url=job.url,
        status="applied",
        source="dashboard",
    )
    return JSONResponse({"ok": True})


@app.post("/api/job/{job_id}/skip")
async def api_skip(job_id: str) -> JSONResponse:
    if not update_job_status(job_id, "skipped"):
        raise HTTPException(404, f"Job {job_id} not found")
    return JSONResponse({"ok": True})


@app.post("/api/job/{job_id}/reset")
async def api_reset(job_id: str) -> JSONResponse:
    """Put a job back in the queue (e.g. you tapped Apply but decided not to
    submit)."""
    if not update_job_status(job_id, "queued"):
        raise HTTPException(404, f"Job {job_id} not found")
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@app.get("/api/profile")
async def api_profile() -> JSONResponse:
    p = get_profile_store().load()
    return JSONResponse({
        "name": f"{p.first_name} {p.last_name}".strip(),
        "email": p.email,
        "phone": p.phone,
        "city": p.city,
        "state": p.state,
        "current_title": p.current_title,
        "years_experience": p.years_of_experience,
        "linkedin": p.linkedin_url,
        "portfolio": p.portfolio_url,
        "resume": Path(p.resume_path).name if p.resume_path else "",
    })


@app.get("/api/latest-draft")
async def api_latest_draft() -> JSONResponse:
    path = PROJECT_ROOT / "data" / "resumes" / "latest_draft.json"
    if not path.exists():
        return JSONResponse({})
    try:
        raw = json.loads(path.read_text())
        return JSONResponse({
            "title": str(raw.get("title", "")),
            "company": str(raw.get("company", "")),
            "recommendation": str(raw.get("recommendation", "")),
            "matched_skills": list(raw.get("matched_skills", []))[:6]
            if isinstance(raw.get("matched_skills"), list)
            else [],
        })
    except Exception as exc:
        log.warning("Could not read latest draft manifest: %s", exc)
        return JSONResponse({})


@app.get("/api/bookmarklet")
async def api_bookmarklet() -> None:
    """Fail closed because live-form automation is outside the safe flow."""
    raise HTTPException(
        410,
        detail={
            "message": "Automated live-form filling is disabled.",
            "next_action": "Generate a paste sheet and submit by hand.",
        },
    )
# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def get_tailscale_ip() -> str | None:
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=3,
        )
        ip = result.stdout.strip().split("\n")[0]
        if ip and ip.startswith("100."):
            return ip
    except Exception:
        pass
    return None


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def run_server(host: str = "127.0.0.1", port: int | None = None) -> None:
    import uvicorn

    from jobpilot.core.config import DEFAULT_SERVE_PORT
    remote = configure_server_access(host)
    uvicorn.run(
        app,
        host=host,
        port=port or DEFAULT_SERVE_PORT,
        log_level="info",
        access_log=not remote,
    )
