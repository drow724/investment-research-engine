from fastapi import APIRouter, HTTPException, Query, Request, status

from investment.interfaces.api.fastapi.runtime.schemas import (
    ErrorResponse,
    JobExecutionResponse,
    ManualJobResponse,
    RuntimeStatusResponse,
)
from investment.runtime.application import AutonomousRuntime
from investment.runtime.domain import ResearchJobExecution

router = APIRouter(tags=["runtime"])


@router.get("/runtime/status", response_model=RuntimeStatusResponse)
def runtime_status(request: Request) -> RuntimeStatusResponse:
    state = _runtime(request).state.state
    return RuntimeStatusResponse(
        engine=state.engine,
        instance_id=state.instance_id,
        status=state.status.value,
        version=state.version,
        started_at=state.started_at,
        reported_at=state.reported_at,
        active_execution_ids=state.active_execution_ids,
        last_successful_execution_id=state.last_successful_execution_id,
        last_failed_execution_id=state.last_failed_execution_id,
        reporting_degraded=state.reporting_degraded,
    )


@router.get("/jobs", response_model=tuple[JobExecutionResponse, ...])
def recent_jobs(
    request: Request, limit: int = Query(default=100, ge=1, le=1000)
) -> tuple[JobExecutionResponse, ...]:
    return tuple(_job(item) for item in _runtime(request).state.executions(limit))


@router.get("/jobs/{execution_id}", response_model=JobExecutionResponse)
def job(request: Request, execution_id: str) -> JobExecutionResponse:
    try:
        return _job(_runtime(request).state.execution(execution_id))
    except FileNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job execution not found") from error


@router.post("/jobs/{job_name}/run", response_model=ManualJobResponse)
def run_job(request: Request, job_name: str) -> ManualJobResponse:
    try:
        execution = _runtime(request).scheduler.run_now(job_name)
    except ValueError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    if not isinstance(execution, ResearchJobExecution):
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "unexpected job result")
    return ManualJobResponse(execution=_job(execution))


def _runtime(request: Request) -> AutonomousRuntime:
    value = getattr(request.app.state, "autonomous_runtime", None)
    if not isinstance(value, AutonomousRuntime):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "runtime is not initialized")
    return value


def _job(value: ResearchJobExecution) -> JobExecutionResponse:
    error = (
        ErrorResponse(
            category=value.error.category.value,
            code=value.error.code,
            message=value.error.message,
        )
        if value.error is not None
        else None
    )
    return JobExecutionResponse(
        execution_id=value.execution_id,
        job_name=value.job_name,
        status=value.status.value,
        scheduled_at=value.scheduled_at,
        started_at=value.started_at,
        finished_at=value.finished_at,
        error=error,
        retry_count=value.retry_count,
        metadata=dict(value.metadata),
    )
