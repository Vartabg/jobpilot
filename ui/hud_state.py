"""Mutable state for the interactive JobPilot HUD."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from jobpilot.core.queue_builder import QueueJob
from jobpilot.gigs.core.models import Gig

Lane = Literal["gig", "job"]


@dataclass
class HudState:
    lane: Lane = "gig"
    gig_index: int = 0
    job_index: int = 0
    text_filter: str = ""
    show_help: bool = False
    status_message: str = ""
    status_until: float = 0.0
    previous_gig_ids: set[str] = field(default_factory=set)
    new_gig_ids: set[str] = field(default_factory=set)
    feed_lines: list[str] = field(default_factory=list)

    def selected_index(self) -> int:
        return self.gig_index if self.lane == "gig" else self.job_index

    def set_selected_index(self, value: int) -> None:
        if self.lane == "gig":
            self.gig_index = value
        else:
            self.job_index = value

    def toggle_lane(self) -> None:
        self.lane = "job" if self.lane == "gig" else "gig"

    def flash(self, message: str, *, until: float) -> None:
        self.status_message = message
        self.status_until = until


@dataclass
class HudData:
    gigs: list[Gig]
    jobs: list[QueueJob]
    gigs_meta: dict[str, Any]
    pipe_rows: list[Any]
    pipe_counts: dict[str, int]