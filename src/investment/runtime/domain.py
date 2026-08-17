"""Runtime state, job execution, and event domain models."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from investment.core.domain.observation import require_utc


class EngineStatus(StrEnum):
    STARTING = "STARTING"
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


class JobStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    SKIPPED_LOCKED = "SKIPPED_LOCKED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    CANCELLED = "CANCELLED"


class EventType(StrEnum):
    ENGINE_STARTED = "ENGINE_STARTED"
    ENGINE_STOPPING = "ENGINE_STOPPING"
    HEARTBEAT = "HEARTBEAT"
    JOB_SCHEDULED = "JOB_SCHEDULED"
    JOB_STARTED = "JOB_STARTED"
    JOB_COMPLETED = "JOB_COMPLETED"
    JOB_FAILED = "JOB_FAILED"
    JOB_SKIPPED = "JOB_SKIPPED"


class ErrorCategory(StrEnum):
    DATA_PROVIDER_ERROR = "DATA_PROVIDER_ERROR"
    DATA_VALIDATION_ERROR = "DATA_VALIDATION_ERROR"
    RESEARCH_ERROR = "RESEARCH_ERROR"
    MODEL_ERROR = "MODEL_ERROR"
    STORAGE_ERROR = "STORAGE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class RuntimeErrorInfo:
    category: ErrorCategory
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ResearchJobExecution:
    execution_id: str
    job_name: str
    status: JobStatus
    scheduled_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: RuntimeErrorInfo | None = None
    retry_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_utc(self.scheduled_at, "scheduled_at")
        if self.started_at is not None:
            require_utc(self.started_at, "started_at")
        if self.finished_at is not None:
            require_utc(self.finished_at, "finished_at")

    @classmethod
    def scheduled(
        cls,
        job_name: str,
        *,
        scheduled_at: datetime | None = None,
        execution_identity: str | None = None,
    ) -> "ResearchJobExecution":
        timestamp = scheduled_at or datetime.now(UTC)
        identity = f"exec-{timestamp:%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}"
        metadata = {"executionIdentity": execution_identity} if execution_identity else {}
        return cls(identity, job_name, JobStatus.SCHEDULED, timestamp, metadata=metadata)

    def start(self, now: datetime | None = None) -> "ResearchJobExecution":
        if self.status is not JobStatus.SCHEDULED:
            raise ValueError("only a scheduled job can start")
        return replace(self, status=JobStatus.RUNNING, started_at=now or datetime.now(UTC))

    def finish(
        self,
        status: JobStatus,
        *,
        error: RuntimeErrorInfo | None = None,
        now: datetime | None = None,
    ) -> "ResearchJobExecution":
        if self.status not in {JobStatus.SCHEDULED, JobStatus.RUNNING}:
            raise ValueError("job is already terminal")
        if status not in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.SKIPPED,
            JobStatus.SKIPPED_LOCKED,
            JobStatus.SKIPPED_DUPLICATE,
            JobStatus.CANCELLED,
        }:
            raise ValueError("invalid terminal job status")
        return replace(self, status=status, finished_at=now or datetime.now(UTC), error=error)


@dataclass(frozen=True, slots=True)
class EngineRuntimeState:
    engine: str
    instance_id: str
    status: EngineStatus
    version: str
    started_at: datetime
    reported_at: datetime
    active_execution_ids: tuple[str, ...] = ()
    last_successful_execution_id: str | None = None
    last_failed_execution_id: str | None = None
    reporting_degraded: bool = False


@dataclass(frozen=True, slots=True)
class EngineEvent:
    event_id: str
    engine: str
    instance_id: str
    event_type: EventType
    occurred_at: datetime
    engine_status: EngineStatus
    version: str
    execution_id: str | None = None
    job_name: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: RuntimeErrorInfo | None = None

    @classmethod
    def create(
        cls,
        state: EngineRuntimeState,
        event_type: EventType,
        execution: ResearchJobExecution | None = None,
        *,
        now: datetime | None = None,
    ) -> "EngineEvent":
        timestamp = now or datetime.now(UTC)
        return cls(
            f"evt-{uuid4().hex}",
            state.engine,
            state.instance_id,
            event_type,
            timestamp,
            state.status,
            state.version,
            execution.execution_id if execution else None,
            execution.job_name if execution else None,
            execution.started_at if execution else None,
            execution.finished_at if execution else None,
            execution.error if execution else None,
        )
