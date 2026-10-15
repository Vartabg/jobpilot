"""Read-only application metadata scans; no candidate profile is accessed."""

from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import STATIC
from .privacy import PrivacyScanError, analyze_fields, scan_greenhouse_url


class FieldMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: str = Field(min_length=1, max_length=2000)
    required: bool | None = None
    options: list[str] = Field(default_factory=list, max_length=500)
    description: str = Field(default="", max_length=8000)


class CapturedForm(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_url: str = Field(default="", max_length=2048)
    coverage: Literal["visible_form"] = "visible_form"
    fields: list[FieldMetadata] = Field(max_length=500)
    notices: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("source_url")
    @classmethod
    def public_location(cls, value):
        return display_url(value)


def display_url(value: str) -> str:
    """Reports don't need a login token, query string, fragment or credentials."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname
        if ":" in host:
            host = "[" + host + "]"
        if parsed.port is not None:
            host += ":" + str(parsed.port)
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        return ""


class JobLinks(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    urls: list[str] = Field(min_length=1, max_length=10)


def install_privacy(app, token: str):
    @app.get("/privacy", include_in_schema=False)
    def privacy_page():
        response = FileResponse(STATIC / "privacy.html")
        response.set_cookie("jobpilot_session", token, httponly=True, samesite="strict")
        return response

    @app.post("/api/privacy/scan")
    def scan(value: JobLinks):
        def one(url):
            if len(url) > 2048:
                return {"source_url": "", "complete": False, "error": "This link is too long. Copy the public job link instead."}
            try:
                return scan_greenhouse_url(url)
            except PrivacyScanError as exc:
                return {"source_url": display_url(url), "complete": False, "error": str(exc)}

        # One failure must not turn an uninspected role into a clean report.
        with ThreadPoolExecutor(max_workers=4) as pool:
            reports = list(pool.map(one, value.urls))
        return {"reports": reports}

    @app.post("/api/privacy/analyze")
    def analyze(value: CapturedForm):
        try:
            return analyze_fields(
                [field.model_dump() for field in value.fields],
                source_url=value.source_url,
                coverage=value.coverage,
                notices=value.notices,
            )
        except PrivacyScanError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/privacy/bookmark")
    def bookmark():
        code = (STATIC / "privacy-capture.js").read_text().strip().removesuffix(";")
        return {
            "url": "javascript:" + quote("(" + code + ")();void(0)", safe=""),
            "instruction": "Drag this link to your bookmarks bar. On an application page, use it to capture the visible questions, then copy the metadata back here. No answers are read or sent.",
        }
