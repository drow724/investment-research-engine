"""Local runtime persistence and best-effort Spring HTTP reporting."""

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from investment.runtime.config import EventRetryPolicy
from investment.runtime.domain import (
    EngineEvent,
    EngineRuntimeState,
    EngineStatus,
    ErrorCategory,
    JobStatus,
    ResearchJobExecution,
    RuntimeErrorInfo,
)


class JsonRuntimeStateRepository:
    def __init__(self, root: str | Path = "runtime/state") -> None:
        self.root = Path(root)

    def save_state(self, state: EngineRuntimeState) -> None:
        self._write(self.root / "engine.json", asdict(state))

    def save_execution(self, execution: ResearchJobExecution) -> None:
        self._write(
            self.root / "executions" / f"{execution.execution_id}.json",
            asdict(execution),
        )

    def list_executions(self, limit: int = 100) -> tuple[ResearchJobExecution, ...]:
        directory = self.root / "executions"
        if not directory.exists():
            return ()
        paths = sorted(directory.glob("*.json"), reverse=True)[:limit]
        return tuple(self._execution(self._read(path)) for path in paths)

    def get_execution(self, execution_id: str) -> ResearchJobExecution:
        return self._execution(self._read(self.root / "executions" / f"{execution_id}.json"))

    def find_execution_by_identity(self, identity: str) -> ResearchJobExecution | None:
        for execution in self.list_executions(10_000):
            if execution.metadata.get("executionIdentity") == identity and execution.status in {
                JobStatus.RUNNING,
                JobStatus.COMPLETED,
            }:
                return execution
        return None

    @staticmethod
    def _execution(value: dict[str, Any]) -> ResearchJobExecution:
        error_value = value.get("error")
        error = (
            RuntimeErrorInfo(
                ErrorCategory(error_value["category"]),
                str(error_value["code"]),
                str(error_value["message"]),
            )
            if isinstance(error_value, dict)
            else None
        )
        return ResearchJobExecution(
            str(value["execution_id"]),
            str(value["job_name"]),
            JobStatus(value["status"]),
            datetime.fromisoformat(value["scheduled_at"]),
            _optional_datetime(value.get("started_at")),
            _optional_datetime(value.get("finished_at")),
            error,
            int(value.get("retry_count", 0)),
            value.get("metadata", {}),
        )

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid runtime state record")
        return value

    @staticmethod
    def _write(path: Path, value: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, default=_json_default, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)


class NullEngineEventPublisher:
    def publish(self, event: EngineEvent) -> None:
        del event


class SqliteJobLock:
    """Single-host process-safe lease; ownership prevents foreign release."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS job_lease (
                       lock_key TEXT PRIMARY KEY,
                       owner_id TEXT NOT NULL,
                       acquired_at TEXT NOT NULL,
                       expires_at TEXT NOT NULL
                   )"""
            )

    def acquire(self, key: str, owner_id: str, ttl: timedelta) -> bool:
        if not key or not owner_id or ttl <= timedelta(0):
            raise ValueError("lease requires key, owner, and positive TTL")
        now = datetime.now(UTC)
        expires = now + ttl
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner_id, expires_at FROM job_lease WHERE lock_key = ?", (key,)
            ).fetchone()
            if row is not None and datetime.fromisoformat(row["expires_at"]) > now:
                return False
            connection.execute(
                """INSERT INTO job_lease(lock_key, owner_id, acquired_at, expires_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(lock_key) DO UPDATE SET
                     owner_id=excluded.owner_id,
                     acquired_at=excluded.acquired_at,
                     expires_at=excluded.expires_at""",
                (key, owner_id, now.isoformat(), expires.isoformat()),
            )
        return True

    def release(self, key: str, owner_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM job_lease WHERE lock_key = ? AND owner_id = ?", (key, owner_id)
            )
        return cursor.rowcount == 1

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection


class HttpEngineEventPublisher:
    """Finite retry publisher; callers decide how to handle final delivery failure."""

    def __init__(
        self,
        endpoint: str,
        retry_policy: EventRetryPolicy | None = None,
        *,
        timeout_seconds: float = 5.0,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.endpoint = endpoint
        self.retry_policy = retry_policy or EventRetryPolicy()
        self.timeout_seconds = timeout_seconds
        self._client = client or httpx.Client()
        self._sleeper = sleeper

    def publish(self, event: EngineEvent) -> None:
        attempts = len(self.retry_policy.delays_seconds) + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._client.post(
                    self.endpoint,
                    json=_event_payload(event),
                    timeout=self.timeout_seconds,
                    headers={"Idempotency-Key": event.event_id},
                )
                response.raise_for_status()
                return
            except httpx.HTTPError as error:
                last_error = error
                if attempt < len(self.retry_policy.delays_seconds):
                    self._sleeper(self.retry_policy.delays_seconds[attempt])
        raise RuntimeError("engine event delivery failed after finite retries") from last_error


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))


def _event_payload(event: EngineEvent) -> dict[str, object]:
    payload: dict[str, object] = {
        "eventId": event.event_id,
        "engine": event.engine,
        "instanceId": event.instance_id,
        "eventType": event.event_type.value,
        "occurredAt": event.occurred_at.isoformat(),
        "engineStatus": event.engine_status.value,
        "version": event.version,
    }
    optional = {
        "executionId": event.execution_id,
        "jobName": event.job_name,
        "startedAt": event.started_at.isoformat() if event.started_at else None,
        "finishedAt": event.finished_at.isoformat() if event.finished_at else None,
    }
    payload.update({key: value for key, value in optional.items() if value is not None})
    if event.error is not None:
        payload["error"] = {
            "category": event.error.category.value,
            "code": event.error.code,
            "message": event.error.message,
        }
    return payload


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, EngineStatus):
        return value.value
    raise TypeError(type(value).__name__)
