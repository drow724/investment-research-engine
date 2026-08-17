"""Composition of autonomous runtime jobs without scheduler-owned research logic."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from investment.crypto.application.intraday_service import CryptoIntradayMarketDataService
from investment.crypto.application.market_data_service import CryptoMarketDataService
from investment.crypto.application.universe_service import CryptoUniverseService
from investment.crypto.domain.timeframe import CandleTimeframe
from investment.runtime.config import EventRetryPolicy, JobExecutionClass, ScheduledJobConfig
from investment.runtime.infrastructure import (
    HttpEngineEventPublisher,
    JsonRuntimeStateRepository,
    NullEngineEventPublisher,
    SqliteJobLock,
)
from investment.runtime.scheduler import ApplicationJobRegistry, APSchedulerRuntime
from investment.runtime.service import JobHandler, RuntimeStateService


@dataclass(slots=True)
class AutonomousRuntime:
    state: RuntimeStateService
    scheduler: APSchedulerRuntime

    def start(self) -> None:
        self.state.start()
        self.scheduler.start()

    def stop(self) -> None:
        self.scheduler.shutdown(wait=True)
        self.state.stop()


def build_autonomous_runtime(
    *,
    instance_id: str,
    state_root: str,
    event_endpoint: str | None,
    event_retry_delays: tuple[float, ...],
    event_timeout_seconds: float,
    heartbeat_cron: str,
    universe_snapshot_cron: str,
    market_sync_cron: str,
    market_sync_pairs: tuple[str, ...],
    market_sync_lookback_days: int,
    intraday_sync_cron: str,
    intraday_sync_lookback_hours: int,
    intraday_maximum_assets: int,
    dynamic_rebalance_cron: str,
    dynamic_rebalance_handler: JobHandler | None,
    dynamic_rebalance_lock_key: str | None,
    observation_outcome_cron: str,
    observation_outcome_handler: JobHandler | None,
    observation_outcome_lock_key: str | None,
    universe_service: CryptoUniverseService,
    market_service: CryptoMarketDataService,
    intraday_service: CryptoIntradayMarketDataService,
) -> AutonomousRuntime:
    publisher = (
        HttpEngineEventPublisher(
            event_endpoint,
            EventRetryPolicy(event_retry_delays),
            timeout_seconds=event_timeout_seconds,
        )
        if event_endpoint
        else NullEngineEventPublisher()
    )
    state = RuntimeStateService(
        instance_id,
        JsonRuntimeStateRepository(state_root),
        publisher,
        job_lock=SqliteJobLock(f"{state_root}/job-leases.sqlite3"),
    )

    def heartbeat() -> None:
        state.heartbeat()

    def capture_universe() -> None:
        universe_service.capture_current()

    def sync_market() -> None:
        end = datetime.now(UTC)
        market_service.sync_pairs(
            market_sync_pairs, end - timedelta(days=market_sync_lookback_days), end
        )

    def sync_intraday() -> None:
        end = datetime.now(UTC)
        results = intraday_service.sync_liquid_universe(
            universe_service.latest(),
            end - timedelta(hours=intraday_sync_lookback_hours),
            end,
            maximum_assets=intraday_maximum_assets,
            timeframe=CandleTimeframe.MINUTE_15,
        )
        failures = [item for item in results if item.status == "FAILED"]
        if failures:
            pairs = ", ".join(item.pair for item in failures)
            raise RuntimeError(f"intraday sync partially failed after retries: {pairs}")

    handlers: dict[str, JobHandler] = {
        "runtime_heartbeat": heartbeat,
        "crypto_universe_snapshot": capture_universe,
        "crypto_market_sync": sync_market,
        "crypto_intraday_15m_sync": sync_intraday,
    }
    configs = [
        ScheduledJobConfig("runtime_heartbeat", heartbeat_cron),
        ScheduledJobConfig(
            "crypto_universe_snapshot",
            universe_snapshot_cron,
            timeout_seconds=60,
            execution_class=JobExecutionClass.STATE_MUTATION,
            lock_key="dataset:crypto-universe:refresh",
        ),
        ScheduledJobConfig(
            "crypto_market_sync",
            market_sync_cron,
            timeout_seconds=1800,
            execution_class=JobExecutionClass.STATE_MUTATION,
            lock_key="dataset:crypto-daily-market:refresh",
        ),
        ScheduledJobConfig(
            "crypto_intraday_15m_sync",
            intraday_sync_cron,
            timeout_seconds=300,
            execution_class=JobExecutionClass.STATE_MUTATION,
            lock_key="dataset:crypto-15m-market:refresh",
        ),
    ]
    if dynamic_rebalance_handler is not None:
        handlers["crypto_dynamic_paper_rebalance"] = dynamic_rebalance_handler
        configs.append(
            ScheduledJobConfig(
                "crypto_dynamic_paper_rebalance",
                dynamic_rebalance_cron,
                timeout_seconds=300,
                execution_class=JobExecutionClass.EXECUTION,
                lock_key=dynamic_rebalance_lock_key,
            )
        )
    if observation_outcome_handler is not None:
        handlers["crypto_observation_outcome_evaluation"] = observation_outcome_handler
        configs.append(
            ScheduledJobConfig(
                "crypto_observation_outcome_evaluation",
                observation_outcome_cron,
                timeout_seconds=300,
                execution_class=JobExecutionClass.STATE_MUTATION,
                lock_key=observation_outcome_lock_key,
            )
        )
    registry = ApplicationJobRegistry(handlers)
    return AutonomousRuntime(state, APSchedulerRuntime(state, registry, tuple(configs)))


def parse_float_tuple(value: str) -> tuple[float, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(isinstance(item, (int, float)) for item in parsed):
        raise ValueError("retry delays must be a JSON number list")
    return tuple(float(item) for item in parsed)


def parse_string_tuple(value: str) -> tuple[str, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("market pairs must be a JSON string list")
    return tuple(parsed)
