import sqlite3
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from threading import Event, Thread

import httpx

from investment.runtime.config import (
    JOB_POLICIES,
    EventRetryPolicy,
    JobExecutionClass,
    ScheduledJobConfig,
)
from investment.runtime.domain import EngineEvent, EngineStatus, EventType, JobStatus
from investment.runtime.infrastructure import (
    HttpEngineEventPublisher,
    JsonRuntimeStateRepository,
    SqliteJobLock,
)
from investment.runtime.scheduler import ApplicationJobRegistry, APSchedulerRuntime
from investment.runtime.service import RuntimeStateService


class RecordingPublisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[EngineEvent] = []
        self.fail = fail

    def publish(self, event: EngineEvent) -> None:
        self.events.append(event)
        if self.fail:
            raise RuntimeError("Spring unavailable")


def _runtime(tmp_path, publisher: RecordingPublisher) -> RuntimeStateService:
    runtime = RuntimeStateService("test-instance", JsonRuntimeStateRepository(tmp_path), publisher)
    runtime.start()
    return runtime


def test_runtime_is_idle_after_start_and_job_completion(tmp_path) -> None:
    publisher = RecordingPublisher()
    runtime = _runtime(tmp_path, publisher)
    observed: list[EngineStatus] = []

    result = runtime.run_job("research", lambda: observed.append(runtime.state.status))

    assert observed == [EngineStatus.RUNNING]
    assert result.status is JobStatus.COMPLETED
    assert runtime.state.status is EngineStatus.IDLE
    assert runtime.state.last_successful_execution_id == result.execution_id


def test_failure_is_classified_and_persisted(tmp_path) -> None:
    runtime = _runtime(tmp_path, RecordingPublisher())

    def fail() -> None:
        raise ValueError("invalid research data")

    result = runtime.run_job("research", fail)

    assert result.status is JobStatus.FAILED
    assert result.error is not None
    assert result.error.code == "VALUEERROR"
    assert runtime.execution(result.execution_id) == result
    assert runtime.state.last_failed_execution_id == result.execution_id


def test_duplicate_running_job_is_skipped(tmp_path) -> None:
    runtime = _runtime(tmp_path, RecordingPublisher())
    entered = Event()
    release = Event()

    def block() -> None:
        entered.set()
        release.wait(2)

    first_result = []
    thread = Thread(target=lambda: first_result.append(runtime.run_job("same", block)))
    thread.start()
    assert entered.wait(1)
    duplicate = runtime.run_job("same", lambda: None)
    release.set()
    thread.join()

    assert duplicate.status is JobStatus.SKIPPED
    assert first_result[0].status is JobStatus.COMPLETED


def test_reporting_failure_does_not_change_successful_job_result(tmp_path) -> None:
    runtime = _runtime(tmp_path, RecordingPublisher(fail=True))

    result = runtime.run_job("research", lambda: None)

    assert result.status is JobStatus.COMPLETED
    assert runtime.state.status is EngineStatus.DEGRADED
    assert runtime.state.reporting_degraded


def test_http_publisher_retries_finitely_with_same_idempotency_key() -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.headers["Idempotency-Key"])
        return httpx.Response(503, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    publisher = HttpEngineEventPublisher(
        "https://spring.test/internal/v1/engines/events",
        EventRetryPolicy((0, 0)),
        client=client,
        sleeper=lambda _: None,
    )
    now = datetime.now(UTC)
    state = _runtime_state(now)
    event = EngineEvent.create(state, EventType.HEARTBEAT)

    with suppress(RuntimeError):
        publisher.publish(event)

    assert attempts == [event.event_id, event.event_id, event.event_id]


def test_heartbeat_events_have_unique_ids_and_current_state(tmp_path) -> None:
    publisher = RecordingPublisher()
    runtime = _runtime(tmp_path, publisher)
    first = runtime.heartbeat()
    second = runtime.heartbeat()

    assert first.event_id != second.event_id
    assert second.engine_status is EngineStatus.IDLE


def test_job_timeout_is_recorded_as_failure(tmp_path) -> None:
    release = Event()
    runtime = _runtime(tmp_path, RecordingPublisher())

    result = runtime.run_job("slow", lambda: release.wait(1), timeout_seconds=0.01)
    release.set()

    assert result.status is JobStatus.FAILED
    assert result.error is not None
    assert result.error.code == "JOB_TIMEOUT"


def test_state_changing_job_policy_is_single_instance_and_coalesced() -> None:
    for execution_class in (JobExecutionClass.STATE_MUTATION, JobExecutionClass.EXECUTION):
        policy = JOB_POLICIES[execution_class]
        assert policy.max_instances == 1
        assert policy.coalesce
        assert policy.lock_required


def test_scheduler_applies_central_policy_to_apscheduler(tmp_path) -> None:
    class FakeScheduler:
        timezone = UTC
        running = False

        def __init__(self) -> None:
            self.jobs: list[dict[str, object]] = []

        def add_job(self, _handler, _trigger, **kwargs) -> None:
            self.jobs.append(kwargs)

        def start(self) -> None:
            self.running = True

    runtime = _runtime(tmp_path, RecordingPublisher())
    scheduler = FakeScheduler()
    adapter = APSchedulerRuntime(
        runtime,
        ApplicationJobRegistry({"read": lambda: None, "mutate": lambda: None}),
        (
            ScheduledJobConfig("read", "* * * * *"),
            ScheduledJobConfig(
                "mutate",
                "* * * * *",
                execution_class=JobExecutionClass.STATE_MUTATION,
                lock_key="dataset:test:build",
            ),
        ),
        scheduler=scheduler,  # type: ignore[arg-type]
    )

    adapter.start()

    assert scheduler.jobs[0]["max_instances"] == 2
    assert scheduler.jobs[1]["max_instances"] == 1
    assert scheduler.jobs[1]["coalesce"] is True


def test_sqlite_lease_is_owner_safe_and_expired_lease_can_be_reacquired(tmp_path) -> None:
    path = tmp_path / "leases.sqlite3"
    lock = SqliteJobLock(path)

    assert lock.acquire("portfolio:a:rebalance", "owner-a", timedelta(minutes=1))
    assert not lock.release("portfolio:a:rebalance", "owner-b")
    assert not lock.acquire("portfolio:a:rebalance", "owner-b", timedelta(minutes=1))
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE job_lease SET expires_at = ? WHERE lock_key = ?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), "portfolio:a:rebalance"),
        )
    assert lock.acquire("portfolio:a:rebalance", "owner-b", timedelta(minutes=1))
    assert lock.release("portfolio:a:rebalance", "owner-b")


def test_scoped_lock_blocks_same_portfolio_but_allows_different_portfolio(tmp_path) -> None:
    lease = SqliteJobLock(tmp_path / "leases.sqlite3")
    runtime_a = RuntimeStateService(
        "a", JsonRuntimeStateRepository(tmp_path / "a"), RecordingPublisher(), job_lock=lease
    )
    runtime_b = RuntimeStateService(
        "b", JsonRuntimeStateRepository(tmp_path / "b"), RecordingPublisher(), job_lock=lease
    )
    runtime_a.start()
    runtime_b.start()
    entered = Event()
    release = Event()

    def block() -> None:
        entered.set()
        release.wait(2)

    first: list[object] = []
    thread = Thread(
        target=lambda: first.append(
            runtime_a.run_job(
                "rebalance",
                block,
                lock_key="portfolio:a:rebalance",
                lock_ttl_seconds=60,
            )
        )
    )
    thread.start()
    assert entered.wait(1)
    same = runtime_b.run_job(
        "rebalance",
        lambda: None,
        lock_key="portfolio:a:rebalance",
        lock_ttl_seconds=60,
    )
    different = runtime_b.run_job(
        "rebalance",
        lambda: None,
        lock_key="portfolio:b:rebalance",
        lock_ttl_seconds=60,
    )
    release.set()
    thread.join()

    assert same.status is JobStatus.SKIPPED_LOCKED
    assert different.status is JobStatus.COMPLETED


def test_lock_releases_after_failure_and_duplicate_identity_is_skipped(tmp_path) -> None:
    lease = SqliteJobLock(tmp_path / "leases.sqlite3")
    runtime = RuntimeStateService(
        "a", JsonRuntimeStateRepository(tmp_path / "state"), RecordingPublisher(), job_lock=lease
    )
    runtime.start()

    failed = runtime.run_job(
        "rebalance",
        lambda: (_ for _ in ()).throw(RuntimeError("during execution")),
        lock_key="portfolio:a:rebalance",
        lock_ttl_seconds=60,
        execution_identity="rebalance:a:2026-08-14T05:00Z",
    )
    completed = runtime.run_job(
        "rebalance",
        lambda: None,
        lock_key="portfolio:a:rebalance",
        lock_ttl_seconds=60,
        execution_identity="rebalance:a:2026-08-14T05:00Z",
    )
    duplicate = runtime.run_job(
        "rebalance",
        lambda: None,
        lock_key="portfolio:a:rebalance",
        lock_ttl_seconds=60,
        execution_identity="rebalance:a:2026-08-14T05:00Z",
    )

    assert failed.status is JobStatus.FAILED
    assert completed.status is JobStatus.COMPLETED
    assert duplicate.status is JobStatus.SKIPPED_DUPLICATE


def _runtime_state(now: datetime):
    from investment.runtime.domain import EngineRuntimeState

    return EngineRuntimeState("bitcoin", "instance", EngineStatus.IDLE, "0.7.0", now, now)
