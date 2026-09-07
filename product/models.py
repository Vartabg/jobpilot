"""Validated product inputs and public API contract."""

from datetime import UTC, date, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Long = Annotated[str, StringConstraints(strip_whitespace=True, max_length=40000)]


def now() -> str:
    return datetime.now(UTC).isoformat()


def safe_url(value: str) -> str:
    if not value:
        return value
    url = urlsplit(value)
    if (
        url.scheme not in {"https", "http"}
        or not url.hostname
        or url.username
        or url.password
        or any(c.isspace() for c in value)
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Use a complete HTTP or HTTPS link without credentials.")
    _ = url.port
    return value


def valid_date(value: str) -> str:
    if value:
        date.fromisoformat(value)
    return value


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Board(Model):
    provider: Literal["greenhouse", "lever", "ashby"]
    slug: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")


class Profile(Model):
    full_name: Short = ""
    first_name: Short = ""
    last_name: Short = ""
    email: Short = ""
    phone: Short = ""
    city: Short = ""
    region: Short = ""
    country: Short = ""
    postal_code: Short = ""
    linkedin: Short = ""
    portfolio: Short = ""
    github: Short = ""
    headline: Short = ""
    resume_text: Long = ""
    skills: list[Short] = Field(default_factory=list, max_length=40)
    keywords: list[Short] = Field(default_factory=list, max_length=20)
    location: Short = ""
    remote_only: bool = False
    boards: list[Board] = Field(default_factory=list, max_length=8)

    _links = field_validator("linkedin", "portfolio", "github")(safe_url)


class Listing(Model):
    id: str = Field(max_length=120)
    company: Short
    title: Short
    url: str = Field(max_length=2048)
    location: str = Field(default="", max_length=1000)
    description: str = Field(default="", max_length=100000)
    provider: Literal["greenhouse", "lever", "ashby", "manual"]
    board: str = Field(default="", max_length=80)
    checked_at: str = Field(default_factory=now)
    available: bool = True

    _url = field_validator("url")(safe_url)


class SourceResult(Model):
    provider: str
    slug: str
    count: int = 0
    error: str = ""


class SearchResult(Model):
    jobs: list[Listing]
    sources: list[SourceResult]


class Fit(Model):
    matched: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    excerpts: list[str] = Field(default_factory=list)
    signals: int = 0


class TrackedJob(Listing):
    status: Literal[
        "saved", "prepared", "applied", "interview", "closed", "skipped"
    ] = "saved"
    note: str = Field(default="", max_length=4000)
    follow_up: str = Field(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    fit: Fit = Field(default_factory=Fit)

    _date = field_validator("follow_up")(valid_date)


class JobUpdate(Model):
    status: Literal["saved", "prepared", "applied", "interview", "closed", "skipped"]
    note: str = Field(default="", max_length=4000)
    follow_up: str = Field(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$")

    _date = field_validator("follow_up")(valid_date)


class Draft(Model):
    job_id: str
    text: str = Field(max_length=100000)
    updated_at: str = Field(default_factory=now)


class ManualJob(Model):
    company: Short
    title: str = Field(min_length=1, max_length=200)
    url: str = Field(default="", max_length=2048)
    description: str = Field(min_length=30, max_length=100000)
    location: Short = ""

    _url = field_validator("url")(safe_url)


class DraftEdit(Model):
    text: str = Field(min_length=1, max_length=100000)
