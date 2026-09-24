import json
import sqlite3
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
    SelectionVariantResult,
    dynamic_policy_for_version,
)
from investment.crypto.derivatives.domain import (
    BtcDerivativesDecisionOverlay,
    DerivativesOverlayAvailability,
    DerivativesOverlayRecommendation,
    SqueezeState,
)
from investment.crypto.domain.market import Asset, AssetKind, MarketCandle, MarketDataBundle
from investment.crypto.domain.portfolio import PortfolioPurpose, TradingPortfolio
from investment.crypto.infrastructure.market_data import InMemoryCryptoMarketDataProvider
from investment.crypto.infrastructure.sqlite_accounting import SqlitePaperPortfolioRepository
from investment.crypto.observation.domain import (
    DecisionAction,
    DecisionMarketContext,
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


def test_execution_deadline_and_identity_are_checked_before_trading(tmp_path):
    service, repository, origin = _service(tmp_path)
    policy = dynamic_policy_for_version("dynamic-intraday-v2.8-momentum")
    experiment = service.start("deadline", "paper-main", policy, started_at=origin)
    assert not service.permits_execution(
        "deadline", "paper-main", policy, now=origin - timedelta(seconds=1)
    )
    assert service.permits_execution("deadline", "paper-main", policy, now=origin)
    assert not service.permits_execution(
        "deadline", "paper-main", policy, now=experiment.planned_end_at
    )
    assert repository.experiment("deadline").status is ObservationStatus.COMPLETED
    assert not service.permits_execution(
        "deadline", "paper-main", policy, now=experiment.planned_end_at + timedelta(days=1)
    )
    service.start("mismatch", "paper-main", policy, started_at=origin)
    with pytest.raises(ValueError, match="identity"):
        service.permits_execution("mismatch", "wrong-portfolio", policy, now=origin)
    assert repository.experiment("mismatch").status is ObservationStatus.INVALIDATED


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


def test_candidate_cohort_and_market_context_roll_back_together(tmp_path) -> None:
    _, repository, origin = _service(tmp_path)
    repository.save_experiment(
        ObservationExperiment(
            "exp",
            "paper-main",
            "dynamic-intraday-v2.1",
            "hash",
            origin,
            origin + timedelta(hours=168),
            ObservationStatus.RUNNING,
            1_000_000,
        )
    )
    context = DecisionMarketContext(
        "exp",
        "decision-1",
        "dynamic-intraday-v2.1",
        "hash",
        origin,
        "{}",
    )
    with repository._connect() as connection:  # noqa: SLF001
        connection.execute(
            """CREATE TRIGGER reject_test_market_context
               BEFORE INSERT ON decision_market_context
               BEGIN SELECT RAISE(ABORT, 'test context failure'); END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="test context failure"):
        repository.save_decision_bundle((_snapshot("exp", origin),), context)

    assert repository.snapshots("exp") == ()
    assert repository.market_contexts("exp") == ()


def test_runtime_health_counts_only_the_experiment_lane(tmp_path) -> None:
    service, repository, origin = _service(tmp_path)
    repository.save_experiment(
        ObservationExperiment(
            "shadow-exp",
            "paper-main",
            "dynamic-intraday-v2.4",
            "hash",
            origin,
            origin + timedelta(hours=168),
            ObservationStatus.RUNNING,
            1_000_000,
        )
    )
    execution_root = tmp_path / "runtime" / "executions"
    execution_root.mkdir(parents=True)
    for index, (job_name, status) in enumerate(
        (
            ("crypto_dynamic_paper_rebalance", "FAILED"),
            ("crypto_dynamic_paper_shadow_rebalance", "FAILED"),
            ("crypto_dynamic_paper_shadow_rebalance", "SKIPPED_DUPLICATE"),
        )
    ):
        (execution_root / f"{index}.json").write_text(
            json.dumps(
                {
                    "job_name": job_name,
                    "status": status,
                    "scheduled_at": (origin + timedelta(minutes=index + 1)).isoformat(),
                }
            ),
            encoding="utf-8",
        )
    service.runtime_state_root = tmp_path / "runtime"
    service.runtime_job_names = {"dynamic-intraday-v2.4": "crypto_dynamic_paper_shadow_rebalance"}

    health = service.health("shadow-exp", now=origin + timedelta(hours=1))

    assert health["failedCycles"] == 1
    assert health["runtimeErrors"] == 1
    assert health["duplicateExecutionAttempts"] == 1


def test_global_guard_reason_is_not_misattributed_to_unrelated_candidate() -> None:
    action, reason = FrozenObservationService._action_reason(  # noqa: SLF001
        "UNRELATEDKRW",
        True,
        0,
        {},
        {},
        (),
        ("NEW_ENTRY_BLOCKED_BY_SELECTION_CONCENTRATION",),
        "ELIGIBLE",
        False,
    )

    assert action is DecisionAction.REJECTED_ENTRY
    assert reason == "NOT_SELECTED_BY_RANK"


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
    assert service.evaluate_pending("exp", now=decision_at + timedelta(hours=2)) == 1
    assert service.evaluate_pending("exp", now=decision_at + timedelta(hours=4)) == 1
    assert service.evaluate_pending("exp", now=decision_at + timedelta(hours=12)) == 1
    assert service.evaluate_pending("exp", now=decision_at + timedelta(hours=24)) == 1
    assert service.evaluate_pending("exp", now=decision_at + timedelta(hours=24)) == 0
    assert {item.horizon_minutes for item in repository.outcomes("exp")} == {
        15,
        30,
        60,
        120,
        240,
        720,
        1440,
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

    assert restarted.evaluate_pending("exp", now=decision_at + timedelta(hours=4)) == 5
    assert {item.horizon_minutes for item in repository.outcomes("exp")} == {
        15,
        30,
        60,
        120,
        240,
    }


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

    assert service.evaluate_pending("exp", now=origin + timedelta(hours=25)) == 7
    assert all(item.status is OutcomeStatus.MISSING_DATA for item in repository.outcomes("exp"))
    names = {item.name for item in fields(DecisionSnapshot)}
    assert not {"forward_return", "mfe", "mae"}.intersection(names)


def test_missing_outcome_waits_for_collection_grace_period(tmp_path) -> None:
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

    assert service.evaluate_pending("exp", now=origin + timedelta(minutes=20)) == 0
    assert repository.outcomes("exp") == ()

    assert service.evaluate_pending("exp", now=origin + timedelta(minutes=46)) == 1
    outcome = repository.outcomes("exp")[0]
    assert outcome.horizon_minutes == 15
    assert outcome.status is OutcomeStatus.MISSING_DATA
    assert outcome.missing_reason == "MARKET_DATA_INVALID"
    assert repository.missing_reason_counts("exp") == {"MARKET_DATA_INVALID": 1}


def test_config_fingerprint_detects_any_frozen_policy_change() -> None:
    policy = DynamicUniversePolicy()
    assert strategy_config_hash(policy) == strategy_config_hash(policy)
    assert strategy_config_hash(policy) != strategy_config_hash(
        replace(policy, minimum_replacement_score_advantage=0.02)
    )
    v22 = dynamic_policy_for_version("dynamic-intraday-v2.2")
    assert strategy_config_hash(policy) != strategy_config_hash(v22)
    assert strategy_config_hash(v22) != strategy_config_hash(replace(v22, momentum_1h_weight=0.40))
    v23 = dynamic_policy_for_version("dynamic-intraday-v2.3")
    assert strategy_config_hash(v22) != strategy_config_hash(v23)
    assert strategy_config_hash(v22) == (
        "2b7663c3ade6de3ed4ce1ff8269013badba33419ae424682ae5f6e8bd47e45be"
    )
    assert strategy_config_hash(v23) == (
        "f277b3636910a2eae6cae1e6f343943b72755b3211f9e1ab0a64fc9f55c9839d"
    )
    assert strategy_config_hash(v23) != strategy_config_hash(
        replace(v23, bullish_market_breadth_minimum=0.70)
    )
    v24 = dynamic_policy_for_version("dynamic-intraday-v2.4")
    assert strategy_config_hash(v24) != strategy_config_hash(v23)
    assert strategy_config_hash(v24) != strategy_config_hash(
        replace(v24, extreme_score_maximum_penalty=0.20)
    )
    assert strategy_config_hash(v24) == (
        "21a7c3b12192d233ef746c60fe88ed8c9aa5cbd7d25f15ad35c5283d41549e50"
    )
    v25 = dynamic_policy_for_version("dynamic-intraday-v2.5")
    assert strategy_config_hash(v25) == (
        "1760aca5e35d45fbec2da677d2a92d060766a95610983dc91d213966a10171f5"
    )
    v26 = dynamic_policy_for_version("dynamic-intraday-v2.6")
    assert strategy_config_hash(v26) == (
        "c237ee87b57de82c667095676d653425672a711dd9b8a0b4e44a78a483e37b85"
    )
    v27 = dynamic_policy_for_version("dynamic-intraday-v2.7")
    assert strategy_config_hash(v27) == (
        "5d09282bcde0b7a1af73396bbc4ed8b94f3a90d5c49f7bd918d024b886b18c34"
    )
    accuracy = dynamic_policy_for_version("dynamic-intraday-v2.7-accuracy-v1")
    assert strategy_config_hash(accuracy) != strategy_config_hash(v27)


def test_interrupted_experiment_health_window_stops_at_interruption(tmp_path) -> None:
    service, _, origin = _service(tmp_path)
    policy = DynamicUniversePolicy()
    service.start("exp", "paper-main", policy, started_at=origin)
    service.interrupt("exp", "superseded", interrupted_at=origin + timedelta(hours=1))

    health = service.health("exp", now=origin + timedelta(hours=10))
    report = service.report("exp", now=origin + timedelta(hours=10))

    assert health["expectedDecisionCycles"] == 4
    assert report["experiment"]["durationHours"] == 1


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
                "BTCKRW",
                True,
                "ELIGIBLE",
                0.02,
                Decimal("100000"),
                Decimal("101"),
                0.011,
                0.022,
                0.033,
                0.0044,
                origin,
                0.03,
                0.01,
                0.001,
                0.002,
                -0.001,
                ("NEW_ENTRY_FEE_ADJUSTED_RETURN_INSUFFICIENT",),
            ),
        ),
        (),
        portfolio,
        (),
        ("TARGET_WEIGHT_CHANGES_BELOW_REBALANCE_THRESHOLD",),
        BtcDerivativesDecisionOverlay(
            decision_as_of=origin + timedelta(minutes=15),
            availability=DerivativesOverlayAvailability.AVAILABLE,
            recommendation=DerivativesOverlayRecommendation.CONFIRM_RISK_ON,
            reason_codes=("DERIVATIVES_RISK_ON_CONFIRMED",),
            snapshot_id="derivatives-snapshot",
            snapshot_available_at=origin + timedelta(minutes=15),
            signal_as_of=origin + timedelta(minutes=15),
            age_seconds=0,
            feature_version="btc-squeeze-v1",
            state=SqueezeState.ACTIVE,
            liquidation_confirmed=False,
        ),
    )
    result = replace(
        result,
        selection_variants=(
            SelectionVariantResult(
                "v2.4-rule-control",
                (SelectedAsset("BTCKRW", 0.02, Decimal("0.3"), "V24_CONTROL_ENTRY"),),
            ),
        ),
    )

    assert service.capture("exp", result, policy) == 1
    snapshot = repository.snapshots("exp")[0]
    assert snapshot.action is DecisionAction.REJECTED_ENTRY
    assert snapshot.reason == "NEW_ENTRY_FEE_ADJUSTED_RETURN_INSUFFICIENT"
    assert snapshot.reference_price == 101
    assert snapshot.momentum_1h == 0.011
    assert snapshot.momentum_4h == 0.022
    assert snapshot.momentum_24h == 0.033
    assert snapshot.volatility == 0.0044
    assert snapshot.reference_at == origin
    assert snapshot.selected_rank == 1
    assert snapshot.raw_score == 0.03
    assert snapshot.score_penalty == 0.01
    assert snapshot.expected_relative_return_1h == 0.001
    assert snapshot.expected_relative_return_4h == 0.002
    assert snapshot.fee_adjusted_expected_return == -0.001
    assert json.loads(snapshot.candidate_reasons_json) == [
        "NEW_ENTRY_FEE_ADJUSTED_RETURN_INSUFFICIENT"
    ]
    contexts = repository.market_contexts("exp")
    assert len(contexts) == 1
    assert json.loads(contexts[0].context_json)["snapshotId"] == "derivatives-snapshot"
    diagnostics = service.diagnostics("exp")
    assert diagnostics["recentMarketContexts"][0]["decisionId"] == contexts[0].decision_id
    assert diagnostics["recentMarketContexts"][0]["context"]["snapshotId"] == "derivatives-snapshot"
    variants = repository.selection_variants("exp", "v2.4-rule-control")
    assert len(variants) == 1
    assert variants[0].selected is True
    assert variants[0].target_position == pytest.approx(0.3)
    assert variants[0].reason == "V24_CONTROL_ENTRY"
    assert service.capture("exp", result, policy) == 0
    assert len(repository.market_contexts("exp")) == 1
    assert len(repository.selection_variants("exp", "v2.4-rule-control")) == 1
    with pytest.raises(ValueError, match="invalidated"):
        service.capture("exp", result, replace(policy, minimum_replacement_score_advantage=0.02))
    assert repository.experiment("exp").status is ObservationStatus.INVALIDATED


def test_score_components_round_trip_and_legacy_outcomes_migrate(tmp_path) -> None:
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
    repository.save_snapshots((_snapshot("exp", origin),))
    with repository._connect() as connection:
        connection.execute(
            "INSERT INTO decision_outcome VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "snapshot-SOL",
                1,
                (origin + timedelta(hours=1)).isoformat(),
                (origin + timedelta(hours=1)).isoformat(),
                "COMPLETED",
                0.1,
                0.2,
                -0.1,
            ),
        )

    migrated = SqliteObservationRepository(repository.path)
    snapshot = migrated.snapshots("exp")[0]
    assert (snapshot.momentum_1h, snapshot.momentum_4h, snapshot.momentum_24h) == (0.01, 0.02, 0.03)
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
