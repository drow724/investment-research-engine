from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from investment.crypto.application.backtest_service import build_universe
from investment.crypto.application.dynamic_paper_rebalance import (
    CandidateAssessment,
    DynamicPaperRebalanceResult,
    DynamicUniversePolicy,
    SelectedAsset,
)
from investment.crypto.domain.market import Asset, AssetKind, MarketCandle, MarketDataBundle
from investment.crypto.domain.portfolio import PortfolioPurpose, TradingPortfolio
from investment.crypto.infrastructure.market_data import InMemoryCryptoMarketDataProvider
from investment.crypto.infrastructure.sqlite_accounting import SqlitePaperPortfolioRepository
from investment.crypto.observation.domain import (
    DecisionAction,
    DecisionSnapshot,
    ObservationExperiment,
    ObservationStatus,
    OutcomeStatus,
)
from investment.crypto.observation.repository import SqliteObservationRepository
from investment.crypto.observation.service import FrozenObservationService, strategy_config_hash


def _service(tmp_path):
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    universe = build_universe(("BTC/KRW", "SOL/KRW"))
    candles = {}
    for pair in universe.pairs:
        values = []
        for index in range(100):
            price = Decimal("100") + Decimal(index)
            values.append(
                MarketCandle(
                    pair,
                    origin + timedelta(minutes=15 * index),
                    origin + timedelta(minutes=15 * (index + 1)),
                    price,
                    price + Decimal("2"),
                    price - Decimal("2"),
                    price + Decimal("1"),
                    Decimal("10"),
                )
            )
        candles[pair.symbol] = tuple(values)
    paper = SqlitePaperPortfolioRepository(tmp_path / "paper.sqlite3")
    paper.create(
        TradingPortfolio(
            "paper-main",
            PortfolioPurpose.PAPER_TRADING,
            Asset("KRW", AssetKind.CASH),
            Decimal("1000000"),
        )
    )
    repository = SqliteObservationRepository(tmp_path / "observation.sqlite3")
    service = FrozenObservationService(
        repository,
        paper,
        InMemoryCryptoMarketDataProvider(MarketDataBundle(universe, candles)),
    )
    return service, repository, origin


def _snapshot(experiment_id: str, at: datetime, asset: str = "SOL") -> DecisionSnapshot:
    return DecisionSnapshot(
        f"snapshot-{asset}",
        experiment_id,
        "decision-1",
        "dynamic-intraday-v2.1",
        "hash",
        at,
        asset,
        f"{asset}KRW",
        DecisionAction.REJECTED_ENTRY,
        "ENTRY_CONFIRMATION_PENDING",
        0.02,
        2,
        True,
        False,
        0,
        0,
        1000000,
        1000000,
        0,
        0,
        101,
        100000,
        at.hour,
        at.weekday(),
        0.01,
        0.02,
        0.03,
        0.004,
        at,
        None,
    )


def test_all_minute_horizons_materialize_independently_and_are_idempotent(tmp_path) -> None:
    service, repository, origin = _service(tmp_path)
    experiment = ObservationExperiment(
        "exp",
        "paper-main",
        "dynamic-intraday-v2.1",
        "hash",
        origin,
        origin + timedelta(hours=168),
        ObservationStatus.RUNNING,
        1000000,
    )
    repository.save_experiment(experiment)
    decision_at = origin + timedelta(minutes=15)
    repository.save_snapshots((_snapshot("exp", decision_at),))

    assert service.evaluate_pending("exp", now=decision_at + timedelta(minutes=14)) == 0
    assert service.evaluate_pending("exp", now=decision_at + timedelta(minutes=15)) == 1
    assert service.evaluate_pending("exp", now=decision_at + timedelta(minutes=30)) == 1
    assert service.evaluate_pending("exp", now=decision_at + timedelta(hours=1)) == 1
    assert service.evaluate_pending("exp", now=decision_at + timedelta(hours=4)) == 1
    assert service.evaluate_pending("exp", now=decision_at + timedelta(hours=12)) == 1
    assert service.evaluate_pending("exp", now=decision_at + timedelta(hours=24)) == 1
    assert service.evaluate_pending("exp", now=decision_at + timedelta(hours=24)) == 0
    assert {item.horizon_minutes for item in repository.outcomes("exp")} == {
        15, 30, 60, 240, 720, 1440
    }
    outcome = repository.outcomes("exp")[2]
    assert outcome.status is OutcomeStatus.COMPLETED
    assert outcome.forward_return == pytest.approx(105 / 101 - 1)
    assert outcome.mfe == pytest.approx(106 / 101 - 1)
    assert outcome.mae == pytest.approx(99 / 101 - 1)


def test_restart_resumes_rejected_candidate_outcome_evaluation(tmp_path) -> None:
    service, repository, origin = _service(tmp_path)
    repository.save_experiment(
        ObservationExperiment(
            "exp",
            "paper-main",
            "dynamic-intraday-v2.1",
            "hash",
            origin,
            origin + timedelta(hours=168),
            ObservationStatus.RUNNING,
            1000000,
        )
    )
    decision_at = origin + timedelta(minutes=15)
    repository.save_snapshots((_snapshot("exp", decision_at),))
    restarted = FrozenObservationService(
        SqliteObservationRepository(repository.path),
        service.paper_repository,
        service.market_data,
    )

    assert restarted.evaluate_pending("exp", now=decision_at + timedelta(hours=4)) == 4
    assert {item.horizon_minutes for item in repository.outcomes("exp")} == {15, 30, 60, 240}


def test_missing_horizon_data_is_explicit_and_decision_has_no_future_fields(tmp_path) -> None:
    service, repository, origin = _service(tmp_path)
    repository.save_experiment(
        ObservationExperiment(
            "exp",
            "paper-main",
            "dynamic-intraday-v2.1",
            "hash",
            origin,
            origin + timedelta(hours=168),
            ObservationStatus.RUNNING,
            1000000,
        )
    )
    repository.save_snapshots((_snapshot("exp", origin, "MISSING"),))

    assert service.evaluate_pending("exp", now=origin + timedelta(hours=24)) == 6
    assert all(item.status is OutcomeStatus.MISSING_DATA for item in repository.outcomes("exp"))
    names = {item.name for item in fields(DecisionSnapshot)}
    assert not {"forward_return", "mfe", "mae"}.intersection(names)


def test_config_fingerprint_detects_any_frozen_policy_change() -> None:
    policy = DynamicUniversePolicy()
    assert strategy_config_hash(policy) == strategy_config_hash(policy)
    assert strategy_config_hash(policy) != strategy_config_hash(
        replace(policy, minimum_replacement_score_advantage=0.02)
    )


def test_capture_persists_candidate_action_and_invalidates_changed_config(tmp_path) -> None:
    service, repository, origin = _service(tmp_path)
    policy = DynamicUniversePolicy()
    service.start("exp", "paper-main", policy, started_at=origin)
    portfolio = service.paper_repository.get("paper-main")
    result = DynamicPaperRebalanceResult(
        "paper-main",
        origin + timedelta(minutes=15),
        origin,
        True,
        Decimal("1000000"),
        (SelectedAsset("BTCKRW", 0.02, Decimal("0.3"), "ENTRY_CONFIRMED"),),
        (
            CandidateAssessment(
                "BTCKRW", True, "ELIGIBLE", 0.02, Decimal("100000"), Decimal("101"),
                0.011, 0.022, 0.033, 0.0044, origin,
            ),
        ),
        (),
        portfolio,
        (),
        ("TARGET_WEIGHT_CHANGES_BELOW_REBALANCE_THRESHOLD",),
    )

    assert service.capture("exp", result, policy) == 1
    snapshot = repository.snapshots("exp")[0]
    assert snapshot.action is DecisionAction.REJECTED_ENTRY
    assert snapshot.reference_price == 101
    assert snapshot.momentum_1h == 0.011
    assert snapshot.momentum_4h == 0.022
    assert snapshot.momentum_24h == 0.033
    assert snapshot.volatility == 0.0044
    assert snapshot.reference_at == origin
    assert snapshot.selected_rank == 1
    with pytest.raises(ValueError, match="invalidated"):
        service.capture("exp", result, replace(policy, minimum_replacement_score_advantage=0.02))
    assert repository.experiment("exp").status is ObservationStatus.INVALIDATED


def test_score_components_round_trip_and_legacy_outcomes_migrate(tmp_path) -> None:
    service, repository, origin = _service(tmp_path)
    repository.save_experiment(
        ObservationExperiment(
            "exp", "paper-main", "dynamic-intraday-v2.1", "hash", origin,
            origin + timedelta(hours=168), ObservationStatus.RUNNING, 1000000,
        )
    )
    repository.save_snapshots((_snapshot("exp", origin),))
    with repository._connect() as connection:
        connection.execute(
            "INSERT INTO decision_outcome VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("snapshot-SOL", 1, (origin + timedelta(hours=1)).isoformat(),
             (origin + timedelta(hours=1)).isoformat(), "COMPLETED", 0.1, 0.2, -0.1),
        )

    migrated = SqliteObservationRepository(repository.path)
    snapshot = migrated.snapshots("exp")[0]
    assert (snapshot.momentum_1h, snapshot.momentum_4h, snapshot.momentum_24h) == (
        0.01, 0.02, 0.03
    )
    assert snapshot.volatility == 0.004
    assert snapshot.reference_at == origin
    assert {item.horizon_minutes for item in migrated.outcomes("exp")} == {60}


def test_zero_trade_partial_report_and_btc_benchmark_do_not_crash(tmp_path) -> None:
    service, _, origin = _service(tmp_path)
    service.start("exp", "paper-main", DynamicUniversePolicy(), started_at=origin)

    report = service.report("exp", now=origin + timedelta(hours=4))

    assert report["trades"]["completedTrades"] == 0
    assert report["trades"]["expectancyNetPnl"] is None
    assert report["performance"]["btcBenchmarkReturn"] == pytest.approx(116 / 101 - 1)


def test_interrupt_preserves_evidence_and_stops_new_capture(tmp_path) -> None:
    service, repository, origin = _service(tmp_path)
    policy = DynamicUniversePolicy()
    service.start("exp", "paper-main", policy, started_at=origin)

    stopped = service.interrupt(
        "exp", "safety gate failed", interrupted_at=origin + timedelta(hours=1)
    )

    assert stopped.status is ObservationStatus.INTERRUPTED
    assert stopped.interruption_reason == "safety gate failed"
    assert stopped.completed_at == origin + timedelta(hours=1)
    assert repository.experiment("exp").status is ObservationStatus.INTERRUPTED
