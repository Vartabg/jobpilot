"""Portable, self-reported career evidence. Parsing never fetches sources."""

import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

Text = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, max_length=4000)
]
Title = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=160),
]


class PacketModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkAccount(PacketModel):
    title: Title
    kind: Literal["project", "practical", "people"]
    story: Text
    contribution: Text
    tools: Text
    validation: Text
    outcome: Text
    source_url: str = Field(strict=True, max_length=2048)

    @field_validator("source_url")
    @classmethod
    def public_link(cls, value: str) -> str:
        if not value:
            return value
        if not value.startswith(("http://", "https://")) or re.search(
            r"[\s\x00-\x1f\x7f]", value
        ):
            raise ValueError("Use a complete HTTP or HTTPS link without spaces.")
        parsed = urlsplit(value)
        _ = parsed.port  # Invalid or out-of-range ports must also be rejected.
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Use an HTTP or HTTPS link without credentials.")
        return value


class Direction(PacketModel):
    title: Text
    why: Text
    gap: Text
    constraints: Text
    next_step: Text


class WorkshopPacket(PacketModel):
    schema_version: Literal["jobpilot.workshop/v1"]
    created_at: AwareDatetime
    review_status: Literal["draft", "wording_confirmed"]
    work: WorkAccount
    direction: Direction

    @field_validator("created_at", mode="before")
    @classmethod
    def timestamp_string(cls, value):
        if not isinstance(value, str):
            raise ValueError("Use an ISO timestamp with timezone.")
        return value
