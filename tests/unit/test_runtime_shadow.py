from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from investment.interfaces.api.fastapi.settings import Settings
from investment.runtime.application import AutonomousRuntime, build_autonomous_runtime
from investment.runtime.config import JobExecutionClass
from investment.runtime.domain import JobStatus
from investment.runtime.service import JobHandler


def test_shadow_settings_are_optional_and_disabled_by_default() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert not settings.runtime_shadow_dynamic_enabled
    assert settings.runtime_shadow_dynamic_paper_execute is False
    assert settings.runtime_shadow_dynamic_rebalance_cron == "6,21,36,51 * * * *"
    assert settings.runtime_paper_execution_model_version == "paper-fill-v1"
    assert settings.runtime_intraday_sync_budget_seconds == 180


@pytest.mark.parametrize("value", [0, 900])
def test_intraday_sync_budget_stays_below_schedule_interval(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, runtime_intraday_sync_budget_seconds=value)


def test_paper_fill_v2_is_explicit_and_unknown_models_fail_closed() -> None:
    settings = Settings(
        _env_file=None,
        runtime_paper_execution_model_version="paper-fill-v2",
    )

    assert settings.runtime_paper_execution_model_version == "paper-fill-v2"
    with pytest.raises(ValidationError, match="paper-fill-v1 or paper-fill-v2"):
        Settings(_env_file=None, runtime_paper_execution_model_version="paper-fill-v3")


def test_blank_shadow_environment_values_disable_the_optional_lane() -> None:
    settings = Settings(
        _env_file=None,
        runtime_shadow_dynamic_strategy_version="",
        runtime_shadow_dynamic_paper_portfolio_id="  ",
        runtime_shadow_observation_experiment_id="",
    )

    assert not settings.runtime_shadow_dynamic_enabled


@pytest.mark.parametrize(
    "values",
    [
        {"runtime_shadow_dynamic_strategy_version": "dynamic-intraday-v2.4"},
        {
            "runtime_shadow_dynamic_strategy_version": "dynamic-intraday-v2.4",
            "runtime_shadow_dynamic_paper_portfolio_id": "paper-v2.4-shadow-main",
        },
    ],
)
def test_shadow_settings_require_all_three_identities(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="requires all identity settings"):
        Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_shadow_settings_enable_only_an_isolated_decision_only_lane() -> None:
    settings = Settings(
        _env_file=None,
        runtime_dynamic_paper_portfolio_id="paper-v2.3-main",
        runtime_observation_experiment_id="paper-v2.3-observation",
        runtime_shadow_dynamic_strategy_version="dynamic-intraday-v2.4",
        runtime_shadow_dynamic_paper_portfolio_id="paper-v2.4-shadow-main",
        runtime_shadow_observation_experiment_id="paper-v2.4-decision-only-20260824",
    )

    assert settings.runtime_shadow_dynamic_enabled

    with pytest.raises(ValidationError, match="Paper execution must be false"):
        Settings(
            _env_file=None,
            runtime_shadow_dynamic_strategy_version="dynamic-intraday-v2.4",
            runtime_shadow_dynamic_paper_portfolio_id="paper-v2.4-shadow-main",
            runtime_shadow_observation_experiment_id="paper-v2.4-decision-only-20260824",
            runtime_shadow_dynamic_paper_execute=True,
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "runtime_dynamic_strategy_version": "dynamic-intraday-v2.4",
                "runtime_shadow_dynamic_strategy_version": "dynamic-intraday-v2.4",
            },
            "strategy versions must differ",
        ),
        (
            {
                "runtime_dynamic_paper_portfolio_id": "shared-paper",
                "runtime_shadow_dynamic_paper_portfolio_id": "shared-paper",
            },
            "Paper portfolio IDs must differ",
        ),
        (
            {
                "runtime_observation_experiment_id": "shared-observation",
                "runtime_shadow_observation_experiment_id": "shared-observation",
            },
            "observation experiment IDs must differ",
        ),
        (
            {
                "runtime_observation_drain_experiment_ids_json": '["shadow-observation"]',
                "runtime_shadow_observation_experiment_id": "shadow-observation",
            },
            "cannot also be a drain experiment",
        ),
    ],
)
def test_shadow_settings_reject_primary_or_drain_identity_collisions(
    overrides: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "runtime_shadow_dynamic_strategy_version": "dynamic-intraday-v2.4",
        "runtime_shadow_dynamic_paper_portfolio_id": "paper-v2.4-shadow-main",
        "runtime_shadow_observation_experiment_id": "shadow-observation",
        **overrides,
    }

    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_runtime_registers_shadow_rebalance_with_its_own_schedule_and_lock(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    runtime = _build_runtime(
        tmp_path,
        primary_handler=lambda: calls.append("primary"),
        shadow_handler=lambda: calls.append("shadow"),
    )

    shadow = next(
        item
        for item in runtime.scheduler.configs
        if item.name == "crypto_dynamic_paper_shadow_rebalance"
    )
    runtime.state.start()
    result = runtime.scheduler.run_now(shadow.name)

    assert shadow.cron == "6,21,36,51 * * * *"
    assert shadow.execution_class is JobExecutionClass.EXECUTION
    assert shadow.lock_key == "portfolio:paper-v2.4-shadow-main:rebalance"
    assert result.status is JobStatus.COMPLETED
    assert calls == ["shadow"]


def test_shadow_failure_does_not_prevent_primary_rebalance(tmp_path: Path) -> None:
    calls: list[str] = []

    def fail_shadow() -> None:
        calls.append("shadow")
        raise RuntimeError("shadow-only failure")

    runtime = _build_runtime(
        tmp_path,
        primary_handler=lambda: calls.append("primary"),
        shadow_handler=fail_shadow,
    )
    runtime.state.start()

    failed = runtime.scheduler.run_now("crypto_dynamic_paper_shadow_rebalance")
    completed = runtime.scheduler.run_now("crypto_dynamic_paper_rebalance")

    assert failed.status is JobStatus.FAILED
    assert completed.status is JobStatus.COMPLETED
    assert calls == ["shadow", "primary"]


def test_runtime_omits_shadow_job_when_no_handler_is_configured(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path, primary_handler=lambda: None, shadow_handler=None)

    assert "crypto_dynamic_paper_shadow_rebalance" not in {
        item.name for item in runtime.scheduler.configs
    }


def _build_runtime(
    tmp_path: Path,
    *,
    primary_handler: JobHandler,
    shadow_handler: JobHandler | None,
) -> AutonomousRuntime:
    unused_service = cast(Any, object())
    return build_autonomous_runtime(
        instance_id="test-instance",
        state_root=str(tmp_path / "runtime"),
        event_endpoint=None,
        event_retry_delays=(),
        event_timeout_seconds=1,
        heartbeat_cron="* * * * *",
        runtime_supervision_handler=None,
        universe_snapshot_cron="5 0 * * *",
        market_sync_cron="15 0 * * *",
        market_sync_pairs=(),
        market_sync_lookback_days=1,
        intraday_sync_cron="1,16,31,46 * * * *",
        intraday_sync_lookback_hours=1,
        intraday_maximum_assets=1,
        derivatives_snapshot_cron="3,18,33,48 * * * *",
        derivatives_snapshot_handler=None,
        dynamic_rebalance_cron="5,20,35,50 * * * *",
        dynamic_rebalance_handler=primary_handler,
        dynamic_rebalance_lock_key="portfolio:paper-v2.3-main:rebalance",
        shadow_dynamic_rebalance_cron="6,21,36,51 * * * *",
        shadow_dynamic_rebalance_handler=shadow_handler,
        shadow_dynamic_rebalance_lock_key=(
            "portfolio:paper-v2.4-shadow-main:rebalance" if shadow_handler else None
        ),
        observation_outcome_cron="10,25,40,55 * * * *",
        observation_outcome_handler=None,
        observation_outcome_lock_key=None,
        strategy_review_cron="12 1 * * *",
        strategy_review_handler=None,
        strategy_review_lock_key=None,
        universe_service=unused_service,
        market_service=unused_service,
        intraday_service=unused_service,
    )
