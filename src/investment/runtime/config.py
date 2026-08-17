"""Configuration records for autonomous jobs and event delivery."""

from dataclasses import dataclass
from enum import StrEnum


class ConcurrentExecutionPolicy(StrEnum):
    SKIP = "SKIP"


class JobExecutionClass(StrEnum):
    READ_ONLY = "READ_ONLY"
    REFRESH = "REFRESH"
    STATE_MUTATION = "STATE_MUTATION"
    EXECUTION = "EXECUTION"


@dataclass(frozen=True, slots=True)
class JobPolicy:
    max_instances: int
    coalesce: bool
    misfire_grace_time_seconds: int
    lock_required: bool
    lock_ttl_seconds: int | None = None


JOB_POLICIES = {
    JobExecutionClass.READ_ONLY: JobPolicy(2, True, 60, False),
    JobExecutionClass.REFRESH: JobPolicy(1, True, 300, False),
    JobExecutionClass.STATE_MUTATION: JobPolicy(1, True, 60, True, 3600),
    JobExecutionClass.EXECUTION: JobPolicy(1, True, 30, True, 900),
}


@dataclass(frozen=True, slots=True)
class ScheduledJobConfig:
    name: str
    cron: str
    enabled: bool = True
    timeout_seconds: int | None = None
    concurrent_policy: ConcurrentExecutionPolicy = ConcurrentExecutionPolicy.SKIP
    execution_class: JobExecutionClass = JobExecutionClass.READ_ONLY
    lock_key: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.cron.split()) != 5:
            raise ValueError("job requires a name and five-field cron expression")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        policy = self.policy
        if policy.lock_required and not self.lock_key:
            raise ValueError("state-changing job requires a scoped lock key")
        if (
            self.timeout_seconds
            and policy.lock_ttl_seconds
            and self.timeout_seconds >= policy.lock_ttl_seconds
        ):
            raise ValueError("job timeout must be shorter than lock lease TTL")

    @property
    def policy(self) -> JobPolicy:
        return JOB_POLICIES[self.execution_class]


@dataclass(frozen=True, slots=True)
class EventRetryPolicy:
    delays_seconds: tuple[float, ...] = (1.0, 5.0, 30.0)

    def __post_init__(self) -> None:
        if any(delay < 0 for delay in self.delays_seconds):
            raise ValueError("event retry delays cannot be negative")
