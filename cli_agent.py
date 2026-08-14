"""JSON-only CLI adapter for model-neutral JobPilot agents."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import typer

from jobpilot.core.agent_contract import AgentEnvelope, AgentServiceError
from jobpilot.core.jobpilot_service import JobPilotService
from jobpilot.core.outcome_service import OutcomeInput

agent_app = typer.Typer(
    help="Versioned JSON contract for authorized AI agents.",
    no_args_is_help=True,
)
_SERVICE = JobPilotService()


def get_service() -> JobPilotService:
    return _SERVICE


def _emit(call: Callable[[], AgentEnvelope]) -> None:
    try:
        payload = call().to_dict()
    except AgentServiceError as exc:
        typer.echo(json.dumps(exc.to_dict(), indent=2))
        raise typer.Exit(2) from exc
    typer.echo(json.dumps(payload, indent=2))


def _job_description(source: str) -> str:
    candidate = Path(source).expanduser()
    try:
        is_file = candidate.is_file()
    except OSError:
        is_file = False
    if not is_file:
        return source
    try:
        return candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AgentServiceError(
            "job_description_unreadable",
            "The job-description file could not be read as UTF-8 text.",
            "Provide a readable text file or pass the description directly.",
        ) from exc


@agent_app.command("capabilities")
def capabilities() -> None:
    """Describe supported operations, side effects, and human boundaries."""
    _emit(lambda: get_service().capabilities())


@agent_app.command("opportunities")
def opportunities(
    refresh: bool = typer.Option(False, "--refresh"),
    ready_only: bool = typer.Option(False, "--ready-only"),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
) -> None:
    """List cached roles or explicitly refresh public ATS sources."""
    if refresh:
        _emit(
            lambda: get_service().refresh_opportunities(
                ready_only=ready_only,
                limit=limit,
            )
        )
        return
    _emit(
        lambda: get_service().list_opportunities(
            ready_only=ready_only,
            limit=limit,
        )
    )


@agent_app.command("assess")
def assess(
    source: str = typer.Argument(..., help="Full JD text or a UTF-8 text-file path."),
    title: str = typer.Option("", "--title"),
) -> None:
    """Assess a full job description against truth-bounded evidence."""
    _emit(
        lambda: get_service().assess_role(
            title=title,
            job_description=_job_description(source),
        )
    )


@agent_app.command("preflight")
def preflight(job_id: str) -> None:
    """Check preparation readiness without filling or submitting an ATS."""
    _emit(lambda: get_service().preflight(job_id))


@agent_app.command("dashboard")
def dashboard() -> None:
    """Read privacy-safe search, relocation, source, and outcome metrics."""
    _emit(lambda: get_service().dashboard())


@agent_app.command("outcome")
def outcome(
    company: str,
    status: str = typer.Option(..., "--status"),
    human_confirmed: bool = typer.Option(False, "--human-confirmed"),
    title: str = typer.Option("", "--title"),
    url: str = typer.Option("", "--url"),
    occurred_at: str | None = typer.Option(None, "--date"),
    channel: str = typer.Option("unknown", "--channel"),
) -> None:
    """Record a real outcome only after explicit human confirmation."""
    request = OutcomeInput(
        company=company,
        title=title,
        status=status,
        human_confirmed=human_confirmed,
        url=url,
        occurred_at=occurred_at,
        acquisition_channel=channel,
    )
    _emit(lambda: get_service().record_outcome(request))
