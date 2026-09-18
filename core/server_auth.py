"""Shared dashboard access links, browser sessions, and API CSRF protection."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import hmac
import os
import secrets
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from jobpilot.core import config
from jobpilot.core.atomic_io import atomic_write_text

COOKIE_NAME = "jobpilot_session"


def get_auth_token() -> str:
    """Load lazily; serialize first creation across both server processes."""
    if config.SERVER_AUTH_TOKEN:
        return config.SERVER_AUTH_TOKEN
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DATA_DIR / "server_token"
    lock = config.DATA_DIR / ".server_token.lock"
    with os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR, 0o600), "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        if path.exists():
            token = path.read_text().strip()
            if token:
                return token
        token = secrets.token_urlsafe(24)
        atomic_write_text(path, token)
        return token


def access_url(host: str, port: int) -> str:
    """Private sign-in URL, intended for local terminal output or its QR."""
    host = f"[{host}]" if ":" in host else host
    return f"http://{host}:{port}/?{urlencode({'token': get_auth_token()})}"


def _matches(candidate: str, expected: str) -> bool:
    return secrets.compare_digest(candidate.encode(), expected.encode())


def _session_value(token: str) -> str:
    return hmac.new(token.encode(), b"jobpilot-browser-session", hashlib.sha256).hexdigest()


def _error(status: int, message: str, action: str) -> JSONResponse:
    return JSONResponse({"error": message, "action": action}, status_code=status)


def install_authentication(app: FastAPI) -> None:
    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        token = await asyncio.to_thread(get_auth_token)
        session = _session_value(token)
        explicit = request.headers.get("x-jobpilot-token")
        bootstrap = request.query_params.get("token")
        if explicit is not None:
            authorized = _matches(explicit, token)
        elif bootstrap is not None:
            authorized = _matches(bootstrap, token)
        else:
            authorized = _matches(request.cookies.get(COOKIE_NAME, ""), session)

        if not authorized:
            response = _error(401, "Sign in to open JobPilot.",
                              "Open the private access link printed by the server on your Mac.")
        elif bootstrap is not None:
            if request.method != "GET" or request.url.path not in ("/", "/install"):
                response = _error(403, "This access link cannot be used here.",
                                  "Open the dashboard access link first.")
            else:
                response = RedirectResponse(request.url.path, status_code=303)
                response.set_cookie(COOKIE_NAME, session, httponly=True,
                                    secure=request.url.scheme == "https", samesite="strict")
        elif explicit is None and request.url.path.startswith("/api/") and request.headers.get("x-jobpilot-request") != "1":
            response = _error(403, "This request could not be verified.",
                              "Reload the dashboard and try again.")
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response
