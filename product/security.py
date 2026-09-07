"""Loopback app security shared by browser and assistant clients."""

import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

from .config import MAX_BODY


def error(message: str, action: str, status: int):
    return JSONResponse({"error": message, "action": action}, status_code=status)


def install_security(app, token: str):
    @app.middleware("http")
    async def protect(request: Request, call_next):
        host = request.headers.get("host", "")
        if request.url.hostname not in {"127.0.0.1", "localhost"}:
            return error(
                "This app accepts local connections only.",
                "Open JobPilot from its launcher.",
                403,
            )
        origin = request.headers.get("origin")
        if origin and origin != "http://" + host:
            return error(
                "This request came from another website.",
                "Return to the JobPilot window.",
                403,
            )
        if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
            return error(
                "This request came from another website.",
                "Return to the JobPilot window.",
                403,
            )
        if request.url.path.startswith("/api/"):
            bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
            cookie = request.cookies.get("jobpilot_session", "")
            if not (
                secrets.compare_digest(token.encode(), bearer.encode())
                or secrets.compare_digest(token.encode(), cookie.encode())
            ):
                return error(
                    "Open JobPilot to start a session.",
                    "Reload the app, or configure your assistant's local API token.",
                    401,
                )
        if request.method in {"POST", "PUT", "PATCH"}:
            length = request.headers.get("content-length", "")
            if not length.isdecimal():
                return error(
                    "The request needs a known size.",
                    "Send a bounded JSON body with Content-Length.",
                    411,
                )
            if int(length) > MAX_BODY:
                return error(
                    "This file or request is too large.",
                    "Use text or a PDF smaller than 2 MiB.",
                    413,
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        )
        return response
