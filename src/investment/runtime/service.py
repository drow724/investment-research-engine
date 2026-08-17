"""Thread-safe runtime orchestration independent from scheduling and Spring."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime, timedelta
from threading import RLock

from investment import __version__
from investment.runtime.domain import (
    EngineEvent,
    EngineRuntimeState,
    EngineStatus,
    ErrorCategory,
    EventType,
    JobStatus,
    ResearchJobExecution,
    RuntimeErrorInfo,
)
from investment.runtime.ports import EngineEventPublisher, JobLock, RuntimeStateRepository

JobHandler = Callable[[], object]


class RuntimeStateService:
    def __init__(
        self,
        instance_id: str,
        repository: RuntimeStateRepository,
        publisher: EngineEventPublisher,
        *,
        engine: str = "investment-research-engine",
        job_lock: JobLock | None = None,
    ) -> None:
        now = datetime.now(UTC)
        self._state = EngineRuntimeState(
            engine, instance_id, EngineStatus.STARTING, __version__, now, now
        )
        self._repository = repository
        self._publisher = publisher
        self._job_lock = job_lock
        self._lock = RLock()
        self._active_jobs: set[str] = set()
        self._accepting = True

    @property
    def state(self) -> EngineRuntimeState:
        with self._lock:
            return self._state

    def start(self) -> None:
        with self._lock:
            self._state = self._with_status(EngineStatus.IDLE)
            self._repository.save_state(self._state)
        self._publish(EventType.ENGINE_STARTED)

    def stop(self) -> None:
        with self._lock:
            self._accepting = False
            self._state = self._with_status(EngineStatus.STOPPED)
            self._repository.save_state(self._state)
        self._publish(EventType.ENGINE_STOPPING)

    def run_job(
        self,
        job_name: str,
        handler: JobHandler,
        *,
        timeout_seconds: int | None = None,
        lock_key: str | None = None,
        lock_ttl_seconds: int | None = None,
        execution_identity: str | None = None,
    ) -> ResearchJobExecution:
        execution = ResearchJobExecution.scheduled(
            job_name, execution_identity=execution_identity
        )
        self._repository.save_execution(execution)
        self._publish(EventType.JOB_SCHEDULED, execution)
        scope = lock_key or job_name
        with self._lock:
            if not self._accepting or scope in self._active_jobs:
                skipped = execution.finish(
                    JobStatus.SKIPPED_LOCKED if lock_key else JobStatus.SKIPPED
                )
                self._repository.save_execution(skipped)
                self._publish(EventType.JOB_SKIPPED, skipped)
                return skipped
            self._active_jobs.add(scope)
        lease_acquired = False
        if lock_key:
            if self._job_lock is None or lock_ttl_seconds is None:
                with self._lock:
                    self._active_jobs.discard(scope)
                raise ValueError("locked job requires a JobLock and lease TTL")
            lease_acquired = self._job_lock.acquire(
                lock_key, execution.execution_id, timedelta(seconds=lock_ttl_seconds)
            )
            if not lease_acquired:
                with self._lock:
                    self._active_jobs.discard(scope)
                skipped = execution.finish(JobStatus.SKIPPED_LOCKED)
                self._repository.save_execution(skipped)
                self._publish(EventType.JOB_SKIPPED, skipped)
                return skipped
        if execution_identity:
            previous = self._repository.find_execution_by_identity(execution_identity)
            if previous is not None:
                if lease_acquired and lock_key and self._job_lock:
                    self._job_lock.release(lock_key, execution.execution_id)
                with self._lock:
                    self._active_jobs.discard(scope)
                skipped = execution.finish(JobStatus.SKIPPED_DUPLICATE)
                self._repository.save_execution(skipped)
                self._publish(EventType.JOB_SKIPPED, skipped)
                return skipped
        with self._lock:
            execution = execution.start()
            self._state = self._state_with_active(execution.execution_id)
            self._repository.save_execution(execution)
            self._repository.save_state(self._state)
        self._publish(EventType.JOB_STARTED, execution)
        try:
            if timeout_seconds is None or lock_key is not None:
                handler()
            else:
                pool = ThreadPoolExecutor(max_workers=1)
                future = pool.submit(handler)
                try:
                    future.result(timeout=timeout_seconds)
                finally:
                    pool.shutdown(wait=False, cancel_futures=True)
            completed = execution.finish(JobStatus.COMPLETED)
            self._complete(completed)
            self._publish(EventType.JOB_COMPLETED, completed)
            return completed
        except FutureTimeoutError:
            failed = execution.finish(
                JobStatus.FAILED,
                error=RuntimeErrorInfo(
                    ErrorCategory.INTERNAL_ERROR,
                    "JOB_TIMEOUT",
                    f"job exceeded configured timeout of {timeout_seconds} seconds",
                ),
            )
            self._complete(failed)
            self._publish(EventType.JOB_FAILED, failed)
            return failed
        except Exception as error:
            failed = execution.finish(JobStatus.FAILED, error=_classify_error(error))
            self._complete(failed)
            self._publish(EventType.JOB_FAILED, failed)
            return failed
        finally:
            if lease_acquired and lock_key and self._job_lock:
                self._job_lock.release(lock_key, execution.execution_id)
            with self._lock:
                self._active_jobs.discard(scope)

    def heartbeat(self) -> EngineEvent:
        return self._publish(EventType.HEARTBEAT)

    def executions(self, limit: int = 100) -> tuple[ResearchJobExecution, ...]:
        return self._repository.list_executions(limit)

    def execution(self, execution_id: str) -> ResearchJobExecution:
        return self._repository.get_execution(execution_id)

    def _complete(self, execution: ResearchJobExecution) -> None:
        with self._lock:
            active_ids = tuple(
                identity
                for identity in self._state.active_execution_ids
                if identity != execution.execution_id
            )
            status = EngineStatus.RUNNING if active_ids else EngineStatus.IDLE
            self._state = EngineRuntimeState(
                self._state.engine,
                self._state.instance_id,
                status,
                self._state.version,
                self._state.started_at,
                datetime.now(UTC),
                active_ids,
                (
                    execution.execution_id
                    if execution.status is JobStatus.COMPLETED
                    else self._state.last_successful_execution_id
                ),
                (
                    execution.execution_id
                    if execution.status is JobStatus.FAILED
                    else self._state.last_failed_execution_id
                ),
                self._state.reporting_degraded,
            )
            self._repository.save_execution(execution)
            self._repository.save_state(self._state)

    def _publish(
        self, event_type: EventType, execution: ResearchJobExecution | None = None
    ) -> EngineEvent:
        event = EngineEvent.create(self.state, event_type, execution)
        try:
            self._publisher.publish(event)
            with self._lock:
                if self._state.reporting_degraded:
                    recovered_status = (
                        EngineStatus.RUNNING
                        if self._state.active_execution_ids
                        else EngineStatus.IDLE
                    )
                    self._state = EngineRuntimeState(
                        self._state.engine,
                        self._state.instance_id,
                        recovered_status,
                        self._state.version,
                        self._state.started_at,
                        datetime.now(UTC),
                        self._state.active_execution_ids,
                        self._state.last_successful_execution_id,
                        self._state.last_failed_execution_id,
                        False,
                    )
                    self._repository.save_state(self._state)
        except Exception:
            with self._lock:
                degraded_status = (
                    self._state.status
                    if self._state.status in {EngineStatus.RUNNING, EngineStatus.STOPPED}
                    else EngineStatus.DEGRADED
                )
                self._state = EngineRuntimeState(
                    self._state.engine,
                    self._state.instance_id,
                    degraded_status,
                    self._state.version,
                    self._state.started_at,
                    datetime.now(UTC),
                    self._state.active_execution_ids,
                    self._state.last_successful_execution_id,
                    self._state.last_failed_execution_id,
                    True,
                )
                self._repository.save_state(self._state)
        return event

    def _with_status(self, status: EngineStatus) -> EngineRuntimeState:
        return EngineRuntimeState(
            self._state.engine,
            self._state.instance_id,
            status,
            self._state.version,
            self._state.started_at,
            datetime.now(UTC),
            self._state.active_execution_ids,
            self._state.last_successful_execution_id,
            self._state.last_failed_execution_id,
            self._state.reporting_degraded,
        )

    def _state_with_active(self, execution_id: str) -> EngineRuntimeState:
        state = self._with_status(EngineStatus.RUNNING)
        return EngineRuntimeState(
            state.engine,
            state.instance_id,
            state.status,
            state.version,
            state.started_at,
            state.reported_at,
            (*state.active_execution_ids, execution_id),
            state.last_successful_execution_id,
            state.last_failed_execution_id,
            state.reporting_degraded,
        )


def _classify_error(error: Exception) -> RuntimeErrorInfo:
    name = type(error).__name__.upper()
    category = (
        ErrorCategory.DATA_PROVIDER_ERROR
        if "HTTP" in name or "TIMEOUT" in name
        else ErrorCategory.DATA_VALIDATION_ERROR
        if isinstance(error, ValueError)
        else ErrorCategory.INTERNAL_ERROR
    )
    message = str(error).replace("Authorization", "[REDACTED]")[:500]
    return RuntimeErrorInfo(category, name, message or "job execution failed")
