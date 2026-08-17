from datetime import datetime

from pydantic import BaseModel, ConfigDict


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class RuntimeApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)


class ErrorResponse(RuntimeApiModel):
    category: str
    code: str
    message: str


class JobExecutionResponse(RuntimeApiModel):
    execution_id: str
    job_name: str
    status: str
    scheduled_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: ErrorResponse | None
    retry_count: int
    metadata: dict[str, object]


class RuntimeStatusResponse(RuntimeApiModel):
    engine: str
    instance_id: str
    status: str
    version: str
    started_at: datetime
    reported_at: datetime
    active_execution_ids: tuple[str, ...]
    last_successful_execution_id: str | None
    last_failed_execution_id: str | None
    reporting_degraded: bool


class ManualJobResponse(RuntimeApiModel):
    execution: JobExecutionResponse
