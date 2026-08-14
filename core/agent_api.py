"""Thin FastAPI adapter for the versioned JobPilot agent contract."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from jobpilot.core.agent_contract import AgentEnvelope, AgentServiceError
from jobpilot.core.jobpilot_service import JobPilotService
from jobpilot.core.outcome_service import OutcomeInput

router = APIRouter(prefix="/api/agent/v1", tags=["agent-v1"])
_SERVICE = JobPilotService()


class AssessRequest(BaseModel):
    title: str = ""
    job_description: str = Field(min_length=1)


class OutcomeRequest(BaseModel):
    company: str = Field(min_length=1)
    title: str = ""
    status: str = Field(min_length=1)
    human_confirmed: bool
    url: str = ""
    occurred_at: str | None = None
    acquisition_channel: str = "unknown"


class AgentSuccessResponse(BaseModel):
    contract_version: str
    operation: str
    ok: Literal[True]
    generated_at: str
    data: dict[str, Any]
    warnings: list[str]
    human_action_required: bool
    human_actions: list[str]


class AgentErrorDetail(BaseModel):
    code: str
    message: str
    action: str


class AgentErrorResponse(BaseModel):
    contract_version: str
    ok: Literal[False]
    error: AgentErrorDetail


AGENT_ERROR_RESPONSES = {
    400: {"model": AgentErrorResponse},
    404: {"model": AgentErrorResponse},
    409: {"model": AgentErrorResponse},
}


def get_service() -> JobPilotService:
    return _SERVICE


def _response(call: Callable[[], AgentEnvelope]) -> JSONResponse:
    try:
        result = call()
    except AgentServiceError as exc:
        return JSONResponse(exc.to_dict(), status_code=exc.status_code)
    return JSONResponse(result.to_dict())


@router.get(
    "/capabilities",
    response_model=AgentSuccessResponse,
    responses=AGENT_ERROR_RESPONSES,
)
def capabilities() -> JSONResponse:
    return _response(lambda: get_service().capabilities())


@router.get(
    "/opportunities",
    response_model=AgentSuccessResponse,
    responses=AGENT_ERROR_RESPONSES,
)
def opportunities(
    ready_only: bool = False,
    limit: int = Query(50, ge=1, le=500),
) -> JSONResponse:
    return _response(
        lambda: get_service().list_opportunities(
            ready_only=ready_only,
            limit=limit,
        )
    )


@router.post(
    "/opportunities/refresh",
    response_model=AgentSuccessResponse,
    responses=AGENT_ERROR_RESPONSES,
)
def refresh_opportunities(
    ready_only: bool = False,
    limit: int = Query(50, ge=1, le=500),
) -> JSONResponse:
    return _response(
        lambda: get_service().refresh_opportunities(
            ready_only=ready_only,
            limit=limit,
        )
    )


@router.post(
    "/assess",
    response_model=AgentSuccessResponse,
    responses=AGENT_ERROR_RESPONSES,
)
def assess(payload: AssessRequest) -> JSONResponse:
    return _response(
        lambda: get_service().assess_role(
            title=payload.title,
            job_description=payload.job_description,
        )
    )


@router.get(
    "/preflight/{job_id}",
    response_model=AgentSuccessResponse,
    responses=AGENT_ERROR_RESPONSES,
)
def preflight(job_id: str) -> JSONResponse:
    return _response(lambda: get_service().preflight(job_id))


@router.get(
    "/dashboard",
    response_model=AgentSuccessResponse,
    responses=AGENT_ERROR_RESPONSES,
)
def dashboard() -> JSONResponse:
    return _response(lambda: get_service().dashboard())


@router.post(
    "/outcomes",
    response_model=AgentSuccessResponse,
    responses=AGENT_ERROR_RESPONSES,
)
def record_outcome(payload: OutcomeRequest) -> JSONResponse:
    values: dict[str, Any] = payload.model_dump()
    return _response(lambda: get_service().record_outcome(OutcomeInput(**values)))
