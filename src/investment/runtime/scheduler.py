"""Configuration-driven APScheduler adapter and application job registry."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from investment.runtime.config import ScheduledJobConfig
from investment.runtime.service import JobHandler, RuntimeStateService


class ApplicationJobRegistry:
    def __init__(self, jobs: Mapping[str, JobHandler]) -> None:
        self._jobs = dict(jobs)

    def resolve(self, name: str) -> JobHandler:
        try:
            return self._jobs[name]
        except KeyError as error:
            raise ValueError(f"unknown autonomous job: {name}") from error

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._jobs))


class APSchedulerRuntime:
    def __init__(
        self,
        runtime: RuntimeStateService,
        registry: ApplicationJobRegistry,
        configs: tuple[ScheduledJobConfig, ...],
        *,
        timezone: str = "UTC",
        scheduler: BackgroundScheduler | None = None,
    ) -> None:
        self.runtime = runtime
        self.registry = registry
        self.configs = configs
        self._scheduler = scheduler or BackgroundScheduler(timezone=timezone)

    def start(self) -> None:
        for config in self.configs:
            if not config.enabled:
                continue
            handler = self.registry.resolve(config.name)
            minute, hour, day, month, day_of_week = config.cron.split()
            trigger = CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
                timezone=self._scheduler.timezone,
            )
            self._scheduler.add_job(
                self._runner(handler, config),
                trigger,
                id=config.name,
                replace_existing=True,
                max_instances=config.policy.max_instances,
                coalesce=config.policy.coalesce,
                misfire_grace_time=config.policy.misfire_grace_time_seconds,
            )
        self._scheduler.start()

    def shutdown(self, *, wait: bool = True) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)

    def run_now(self, job_name: str) -> object:
        config = next((item for item in self.configs if item.name == job_name), None)
        if config is None:
            raise ValueError(f"job is not configured: {job_name}")
        return self._run(self.registry.resolve(job_name), config)

    def _runner(self, handler: JobHandler, config: ScheduledJobConfig) -> Callable[[], object]:
        def run() -> object:
            return self._run(handler, config)

        return run

    def _run(self, handler: JobHandler, config: ScheduledJobConfig) -> object:
        identity = None
        if config.policy.lock_required:
            window = datetime.now(UTC).replace(second=0, microsecond=0)
            identity = f"{config.name}:{window.isoformat()}"
        return self.runtime.run_job(
            config.name,
            handler,
            timeout_seconds=config.timeout_seconds,
            lock_key=config.lock_key,
            lock_ttl_seconds=config.policy.lock_ttl_seconds,
            execution_identity=identity,
        )
