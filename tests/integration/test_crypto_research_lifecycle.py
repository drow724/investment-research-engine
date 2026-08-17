from datetime import UTC, datetime

import pytest

from investment.crypto.application.research_lifecycle_service import (
    CreateStrategyExperimentCommand,
    CryptoResearchLifecycleService,
)
from investment.crypto.domain.research import (
    ExperimentStatus,
    ResearchPeriod,
    ValidationMethod,
    VersionStatus,
)
from investment.crypto.infrastructure.market_data import InMemoryCryptoMarketDataProvider
from investment.crypto.infrastructure.research_repository import JsonResearchLifecycleRepository
from tests.crypto_fixtures import crypto_bundle

PARAMETERS = {
    "momentum_window_days": 30,
    "maximum_positions": 3,
    "maximum_asset_weight": "0.5",
    "rebalance_days": 7,
    "fee_rate": "0.0005",
    "slippage_rate": "0.001",
}


def _command() -> CreateStrategyExperimentCommand:
    return CreateStrategyExperimentCommand(
        "Changing the momentum window improves risk-adjusted returns",
        "cross_sectional_momentum",
        dict(PARAMETERS),
        ("BTC/KRW", "ETH/KRW", "SOL/KRW"),
        ResearchPeriod(datetime(2023, 8, 1, tzinfo=UTC), datetime(2024, 8, 1, tzinfo=UTC)),
        ResearchPeriod(datetime(2024, 8, 2, tzinfo=UTC), datetime(2025, 4, 1, tzinfo=UTC)),
        ValidationMethod.WALK_FORWARD,
    )


def test_strategy_candidate_requires_manual_promotion_and_is_reproducible(tmp_path) -> None:
    repository = JsonResearchLifecycleRepository(tmp_path / "lifecycle")
    service = CryptoResearchLifecycleService(
        InMemoryCryptoMarketDataProvider(crypto_bundle(1000)), repository
    )
    champion = service.initialize_strategy("cross_sectional_momentum", dict(PARAMETERS))
    original_json = champion.parameters_json
    experiment = service.create_experiment(_command())

    assert experiment.status is ExperimentStatus.DRAFT
    assert experiment.candidate_strategy_version == "cross_sectional_momentum:v2"
    assert repository.active_strategy("cross_sectional_momentum").version_id == champion.version_id
    assert repository.get_strategy(champion.version_id).parameters_json == original_json

    with pytest.raises(ValueError, match="invalid experiment transition"):
        service.run(experiment.experiment_id)

    service.mark_ready(experiment.experiment_id)
    result = service.run(experiment.experiment_id)

    assert result.experiment.status is ExperimentStatus.CANDIDATE
    assert result.experiment.dataset_snapshot_id == result.champion_run.dataset_snapshot_id
    assert result.challenger_run.dataset_snapshot_id == result.champion_run.dataset_snapshot_id
    assert repository.get_snapshot(result.champion_run.dataset_snapshot_id).checksum
    assert repository.active_strategy("cross_sectional_momentum").version_id == champion.version_id
    with pytest.raises(ValueError, match="invalid experiment transition"):
        service.run(experiment.experiment_id)

    promoted = service.promote(experiment.experiment_id, "researcher@example.com")
    assert promoted.status is ExperimentStatus.PROMOTED
    assert promoted.approved_by == "researcher@example.com"
    assert repository.active_strategy("cross_sectional_momentum").version_id.endswith(":v2")
    assert repository.get_strategy(champion.version_id).status is VersionStatus.RETIRED

    restored = JsonResearchLifecycleRepository(tmp_path / "lifecycle")
    assert restored.get_experiment(experiment.experiment_id) == promoted


def test_rejected_candidate_cannot_become_active(tmp_path) -> None:
    repository = JsonResearchLifecycleRepository(tmp_path / "lifecycle")
    service = CryptoResearchLifecycleService(
        InMemoryCryptoMarketDataProvider(crypto_bundle(500)), repository
    )
    champion = service.initialize_strategy("cross_sectional_momentum", dict(PARAMETERS))
    experiment = service.create_experiment(_command())
    rejected = service.reject(experiment.experiment_id)

    assert rejected.status is ExperimentStatus.REJECTED
    assert repository.active_strategy("cross_sectional_momentum") == champion
    with pytest.raises(ValueError, match="only a validated candidate"):
        service.promote(experiment.experiment_id, "approver")


def test_failed_experiment_does_not_change_active_strategy(tmp_path) -> None:
    class FailingProvider:
        def fetch(self, universe, start, end):
            raise ValueError("market data unavailable")

    repository = JsonResearchLifecycleRepository(tmp_path / "lifecycle")
    service = CryptoResearchLifecycleService(FailingProvider(), repository)
    champion = service.initialize_strategy("cross_sectional_momentum", dict(PARAMETERS))
    experiment = service.create_experiment(_command())
    service.mark_ready(experiment.experiment_id)

    with pytest.raises(ValueError, match="market data unavailable"):
        service.run(experiment.experiment_id)

    assert service.get(experiment.experiment_id).status is ExperimentStatus.FAILED
    assert repository.active_strategy("cross_sectional_momentum") == champion
