import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from investment.crypto.application.dynamic_paper_rebalance import (
    CROWDING_ONLY_VARIANT_ID,
    MOMENTUM_ONLY_VARIANT_ID,
    PRODUCTION_GATE_VARIANT_ID,
    RAW_DERIVATIVES_VARIANT_ID,
    V24_RULE_CONTROL_VARIANT_ID,
    V25_RULE_CONTROL_VARIANT_ID,
    V26_RULE_CONTROL_VARIANT_ID,
    CandidateAssessment,
    DynamicPaperRebalanceCommand,
    DynamicPaperRebalanceService,
    DynamicUniversePolicy,
    dynamic_policy_for_version,
)
from investment.crypto.derivatives.domain import (
    BasisSource,
    BtcDerivativesDecisionOverlay,
    CrowdingSide,
    CrowdingState,
    DerivativesOverlayAvailability,
    DerivativesOverlayRecommendation,
    SqueezeState,
)
from investment.crypto.domain.accounting import PaperRebalanceDecisionRecord
from investment.crypto.domain.market import (
    Asset,
    AssetKind,
    MarketCandle,
    MarketDataBundle,
)
from investment.crypto.domain.portfolio import PortfolioPurpose, TradingPortfolio
from investment.crypto.domain.universe import (
    UniverseHistory,
    UniverseMember,
    UniverseSnapshot,
)
from investment.crypto.infrastructure.market_data import InMemoryCryptoMarketDataProvider
from investment.crypto.infrastructure.paper_exchange import PaperExchangeGatewayFactory
from investment.crypto.infrastructure.sqlite_accounting import SqlitePaperPortfolioRepository
from tests.crypto_fixtures import crypto_bundle


def _setup(tmp_path):
    source = crypto_bundle(220)
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    candles = {
        symbol: tuple(
            MarketCandle(
                candle.pair,
                origin + timedelta(minutes=15 * index),
                origin + timedelta(minutes=15 * (index + 1)),
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
            )
            for index, candle in enumerate(values)
        )
        for symbol, values in source.candles.items()
    }
    bundle = MarketDataBundle(source.universe, candles)
    observed = origin
    snapshot = UniverseSnapshot(
        observed,
        "upbit",
        tuple(UniverseMember(pair, False, "upbit", observed) for pair in source.universe.pairs),
    )
    repository = SqlitePaperPortfolioRepository(tmp_path / "paper.sqlite3")
    repository.create(
        TradingPortfolio(
            "dynamic-paper",
            PortfolioPurpose.PAPER_TRADING,
            Asset("KRW", AssetKind.CASH),
            Decimal("1000000"),
        )
    )
    service = DynamicPaperRebalanceService(
        UniverseHistory((snapshot,)),
        InMemoryCryptoMarketDataProvider(bundle),
        repository,
        PaperExchangeGatewayFactory(),
        DynamicUniversePolicy(
            minimum_history_bars=100,
            liquidity_lookback_bars=50,
            maximum_candidates=3,
            maximum_positions=2,
            required_entry_confirmations=1,
        ),
    )
    return service, repository, origin + timedelta(minutes=15 * 220)


def test_dynamic_rebalance_selects_new_assets_outside_current_holdings(tmp_path) -> None:
    service, repository, as_of = _setup(tmp_path)

    result = service.run(DynamicPaperRebalanceCommand("dynamic-paper", as_of))

    assert result.dry_run
    assert result.selected
    assert all(item.score > service.policy.round_trip_cost_hurdle for item in result.selected)
    assert all(item.side.value == "BUY" for item in result.orders)
    assert repository.get("dynamic-paper").positions == ()
    assert {item.pair for item in result.selected}.issubset({"BTCKRW", "ETHKRW", "SOLKRW"})


def test_preview_does_not_advance_confirmation_or_decision_history(tmp_path) -> None:
    service, repository, as_of = _setup(tmp_path)

    result = service.run(
        DynamicPaperRebalanceCommand(
            "dynamic-paper",
            as_of,
            execute=False,
            persist_decision=False,
        )
    )

    assert result.dry_run
    assert repository.list_rebalance_decisions("dynamic-paper") == ()


def test_dynamic_paper_execution_is_explicit_and_idempotent(tmp_path) -> None:
    service, repository, as_of = _setup(tmp_path)
    command = DynamicPaperRebalanceCommand("dynamic-paper", as_of, execute=True)

    first = service.run(command)
    second = service.run(command)

    assert not first.dry_run
    assert first.final_portfolio.positions
    assert second.final_portfolio == repository.get("dynamic-paper")
    assert all(item.status == "PAPER_FILLED" for item in first.orders)
    executions = repository.list_executions("dynamic-paper")
    assert len(executions) == len(first.orders)
    assert sum((item.fee for item in executions), Decimal("0")) > 0
    decisions = repository.list_rebalance_decisions("dynamic-paper")
    assert decisions[0].strategy_version == "dynamic-intraday-v2.1"
    assert decisions[0].status == "EXECUTED"
    assert "ORDERS_CREATED_FOR_TARGET_WEIGHT_CHANGES" in decisions[0].decision_reasons


def test_expired_execution_deadline_cannot_create_fills(tmp_path) -> None:
    service, repository, as_of = _setup(tmp_path)
    with pytest.raises(ValueError, match="deadline"):
        service.run(
            DynamicPaperRebalanceCommand(
                "dynamic-paper",
                as_of,
                execute=True,
                execution_deadline=as_of,
            )
        )
    assert repository.list_executions("dynamic-paper") == ()


def test_full_exit_uses_exact_position_quantity_without_dust(tmp_path) -> None:
    service, repository, as_of = _setup(tmp_path)
    service.run(DynamicPaperRebalanceCommand("dynamic-paper", as_of, execute=True))
    service.policy = replace(
        service.policy,
        entry_score_hurdle=999.0,
        hold_score_hurdle=999.0,
        exit_score_hurdle=999.0,
    )

    result = service.run(
        DynamicPaperRebalanceCommand("dynamic-paper", as_of + timedelta(minutes=15), execute=True)
    )

    assert result.final_portfolio.positions == ()
    assert all(item.side.value == "SELL" for item in result.orders)


def test_daily_risk_budget_blocks_new_buys_but_keeps_decision_log(tmp_path) -> None:
    service, repository, as_of = _setup(tmp_path)
    service.policy = replace(service.policy, maximum_daily_turnover_fraction=Decimal("0"))

    result = service.run(DynamicPaperRebalanceCommand("dynamic-paper", as_of, execute=True))

    assert "DAILY_TURNOVER_BUDGET_EXHAUSTED" in result.risk_violations
    assert "NEW_BUYS_BLOCKED_BY_DAILY_RISK_BUDGET" in result.decision_reasons
    assert not result.orders
    decision = repository.list_rebalance_decisions("dynamic-paper")[0]
    assert decision.risk_violations
    assert decision.decision_reasons == result.decision_reasons


def test_recently_sold_asset_is_excluded_during_reentry_cooldown(tmp_path) -> None:
    service, _, _ = _setup(tmp_path)
    assessments = (
        CandidateAssessment("BTCKRW", True, "ELIGIBLE", 0.05, Decimal("100"), Decimal("1")),
    )

    selected, reasons = service._select(  # noqa: SLF001
        assessments,
        held_pairs=set(),
        previous_scores={"BTCKRW": 0.04},
        recent_exits={"BTCKRW"},
    )

    assert selected == ()
    assert reasons == ("NEW_ENTRY_BLOCKED_BY_REENTRY_COOLDOWN",)


def test_new_asset_must_meaningfully_outscore_held_asset_to_replace_it(tmp_path) -> None:
    service, _, _ = _setup(tmp_path)
    service.policy = replace(
        service.policy,
        maximum_positions=2,
        minimum_replacement_score_advantage=0.01,
    )
    held = (
        CandidateAssessment("BTCKRW", True, "ELIGIBLE", 0.03, Decimal("100"), Decimal("1")),
        CandidateAssessment("ETHKRW", True, "ELIGIBLE", 0.02, Decimal("90"), Decimal("1")),
    )
    weak_challenger = CandidateAssessment(
        "SOLKRW", True, "ELIGIBLE", 0.025, Decimal("80"), Decimal("1")
    )
    strong_challenger = replace(weak_challenger, score=0.031)

    selected, reasons = service._select(  # noqa: SLF001
        held + (weak_challenger,),
        held_pairs={"BTCKRW", "ETHKRW"},
        previous_scores={"SOLKRW": 0.025},
        recent_exits=set(),
    )
    replaced, _ = service._select(  # noqa: SLF001
        held + (strong_challenger,),
        held_pairs={"BTCKRW", "ETHKRW"},
        previous_scores={"SOLKRW": 0.031},
        recent_exits=set(),
    )

    assert {item.pair for item in selected} == {"BTCKRW", "ETHKRW"}
    assert "REPLACEMENT_SCORE_ADVANTAGE_INSUFFICIENT" in reasons
    assert {item.pair for item in replaced} == {"BTCKRW", "SOLKRW"}


def test_v22_rank_score_prefers_calm_pullback_and_blocks_pump_chasing(tmp_path) -> None:
    service, _, _ = _setup(tmp_path)
    service.policy = replace(
        dynamic_policy_for_version("dynamic-intraday-v2.2"),
        required_entry_confirmations=1,
        minimum_history_bars=100,
        liquidity_lookback_bars=50,
    )
    assessments = (
        CandidateAssessment(
            "CALMKRW",
            True,
            "ELIGIBLE",
            None,
            Decimal("300"),
            Decimal("100"),
            -0.005,
            -0.01,
            0.01,
            0.003,
        ),
        CandidateAssessment(
            "MIDKRW",
            True,
            "ELIGIBLE",
            None,
            Decimal("200"),
            Decimal("100"),
            0.0,
            0.0,
            0.02,
            0.006,
        ),
        CandidateAssessment(
            "PUMPKRW",
            True,
            "ELIGIBLE",
            None,
            Decimal("100"),
            Decimal("100"),
            0.02,
            0.04,
            0.08,
            0.015,
        ),
    )

    scored = service._score_assessments(assessments)  # noqa: SLF001
    scores = {item.pair: item.score for item in scored}
    selected, reasons = service._select(scored, set(), {}, set())  # noqa: SLF001

    assert scores["CALMKRW"] is not None
    assert scores["PUMPKRW"] is not None
    assert float(scores["CALMKRW"] or 0) > float(scores["PUMPKRW"] or 0)
    assert {item.pair for item in selected} == {"CALMKRW"}
    assert "NEW_ENTRY_BLOCKED_BY_SHORT_TERM_SPIKE_GUARD" in reasons


def test_v22_requires_three_consecutive_confirmations(tmp_path) -> None:
    service, _, _ = _setup(tmp_path)
    service.policy = dynamic_policy_for_version("dynamic-intraday-v2.2")
    candidate = CandidateAssessment(
        "CALMKRW",
        True,
        "ELIGIBLE",
        0.75,
        Decimal("100"),
        Decimal("100"),
        -0.005,
        -0.01,
        0.01,
        0.003,
    )

    waiting, _ = service._select(  # noqa: SLF001
        (candidate,), set(), {"CALMKRW": 0.75}, set(), confirmation_counts={"CALMKRW": 1}
    )
    confirmed, _ = service._select(  # noqa: SLF001
        (candidate,), set(), {"CALMKRW": 0.75}, set(), confirmation_counts={"CALMKRW": 2}
    )

    assert waiting == ()
    assert {item.pair for item in confirmed} == {"CALMKRW"}


def test_v23_expands_turnover_only_for_broad_btc_bull_regime(tmp_path) -> None:
    service, _, _ = _setup(tmp_path)
    service.policy = dynamic_policy_for_version("dynamic-intraday-v2.3")
    bullish = tuple(
        CandidateAssessment(
            "BTCKRW" if index == 0 else f"ASSET{index}KRW",
            True,
            "ELIGIBLE",
            0.7,
            Decimal(20 - index),
            Decimal("100"),
            -0.002,
            0.02,
            0.04,
            0.004,
        )
        for index in range(10)
    )

    expanded, expanded_reasons = service._turnover_budget(bullish)  # noqa: SLF001
    base, base_reasons = service._turnover_budget(  # noqa: SLF001
        (replace(bullish[0], momentum_4h=0.005), *bullish[1:])
    )

    assert expanded == Decimal("3")
    assert "DYNAMIC_TURNOVER_BULLISH_REGIME_ACTIVE" in expanded_reasons
    assert base == Decimal("2")
    assert "DYNAMIC_TURNOVER_BASE_REGIME_ACTIVE" in base_reasons


def test_v23_preserves_fee_and_loss_limits_while_recycling_sell_turnover() -> None:
    v22 = dynamic_policy_for_version("dynamic-intraday-v2.2")
    v23 = dynamic_policy_for_version("dynamic-intraday-v2.3")

    assert v23.maximum_daily_turnover_fraction == v22.maximum_daily_turnover_fraction
    assert v23.bullish_daily_turnover_fraction == Decimal("3")
    assert v23.turnover_sell_weight == Decimal("0.50")
    assert v23.maximum_daily_fee_fraction == v22.maximum_daily_fee_fraction
    assert v23.maximum_daily_realized_loss_fraction == v22.maximum_daily_realized_loss_fraction


def test_v24_applies_quadratic_penalty_and_calibrates_return_units(tmp_path) -> None:
    service, _, _ = _setup(tmp_path)
    service.policy = dynamic_policy_for_version("dynamic-intraday-v2.4")
    candidate = CandidateAssessment("TESTKRW", True, "ELIGIBLE", None, Decimal("100"), Decimal("1"))

    threshold = service._with_score_evidence(candidate, 0.80)  # noqa: SLF001
    extreme = service._with_score_evidence(candidate, 1.0)  # noqa: SLF001

    assert threshold.score == pytest.approx(0.80)
    assert threshold.score_penalty == 0.0
    assert extreme.raw_score == 1.0
    assert extreme.score_penalty == pytest.approx(0.25)
    assert extreme.score == pytest.approx(0.75)
    assert extreme.expected_relative_return_1h == pytest.approx(0.0025)
    assert extreme.expected_relative_return_4h == pytest.approx(0.004)
    assert extreme.fee_adjusted_expected_return == pytest.approx(0.0005)


def test_v24_excludes_stale_market_data_from_selection_and_forward_outcomes(tmp_path) -> None:
    service, _, as_of = _setup(tmp_path)
    service.policy = replace(
        dynamic_policy_for_version("dynamic-intraday-v2.4"),
        minimum_history_bars=100,
        liquidity_lookback_bars=50,
        required_entry_confirmations=1,
    )

    result = service.run(
        DynamicPaperRebalanceCommand("dynamic-paper", as_of + timedelta(minutes=31))
    )

    assert result.selected == ()
    assert result.assessments
    assert {item.reason for item in result.assessments} == {"STALE_MARKET_DATA"}
    assert all(item.latest_price is None for item in result.assessments)


@pytest.mark.parametrize(
    ("age", "stale"),
    [
        (timedelta(minutes=19, seconds=59), False),
        (timedelta(minutes=20), False),
        (timedelta(minutes=20, seconds=1), True),
    ],
)
def test_v27_accuracy_patch_entry_freshness_has_an_inclusive_20_minute_boundary(
    tmp_path, age, stale
) -> None:
    service, _, as_of = _setup(tmp_path)
    service.policy = replace(
        dynamic_policy_for_version("dynamic-intraday-v2.7-accuracy-v1"),
        minimum_history_bars=100,
        liquidity_lookback_bars=50,
        required_entry_confirmations=1,
        derivatives_overlay_mode="SHADOW",
        derivatives_minimum_funding_rate=None,
        derivatives_minimum_basis_input_rate=None,
        derivatives_minimum_global_long_short_ratio=None,
        crowding_overlay_mode="DISABLED",
        crowding_maximum_long_score=None,
        crowding_maximum_bearish_unwind_score=None,
    )

    result = service.run(DynamicPaperRebalanceCommand("dynamic-paper", as_of + age))

    assert result.assessments
    referenced = tuple(item for item in result.assessments if item.reference_at is not None)
    assert referenced
    assert all(item.reference_age_seconds == age.total_seconds() for item in referenced)
    if stale:
        assert result.orders == ()
        assert {item.reason for item in result.assessments} == {"STALE_MARKET_DATA"}
    else:
        assert any(item.eligible for item in result.assessments)


def test_v27_accuracy_patch_stale_holding_fails_safe_without_forced_sell(tmp_path) -> None:
    service, repository, as_of = _setup(tmp_path)
    service.run(DynamicPaperRebalanceCommand("dynamic-paper", as_of, execute=True))
    before = repository.get("dynamic-paper")
    assert before.positions
    service.policy = dynamic_policy_for_version("dynamic-intraday-v2.7-accuracy-v1")

    with pytest.raises(ValueError, match="stale 15m market data"):
        service.run(DynamicPaperRebalanceCommand("dynamic-paper", as_of + timedelta(minutes=31)))

    assert repository.get("dynamic-paper").positions == before.positions
    assert len(repository.list_executions("dynamic-paper")) == len(before.positions)


def test_v27_accuracy_persists_candidate_and_entry_eligible_confirmation_evidence(
    tmp_path,
) -> None:
    service, repository, as_of = _setup(tmp_path)
    service.policy = replace(
        dynamic_policy_for_version("dynamic-intraday-v2.7-accuracy-v1"),
        required_entry_confirmations=1,
        minimum_history_bars=100,
        liquidity_lookback_bars=50,
        derivatives_overlay_mode="SHADOW",
        crowding_overlay_mode="DISABLED",
        entry_score_hurdle=0.0,
        expected_return_calibration_mode="DISABLED",
        minimum_entry_momentum_1h=None,
        minimum_entry_momentum_4h=None,
        maximum_entry_momentum_4h=None,
        maximum_entry_volatility=None,
    )

    result = service.run(DynamicPaperRebalanceCommand("dynamic-paper", as_of))
    decision = repository.list_rebalance_decisions("dynamic-paper")[0]
    assessments = json.loads(decision.assessments_json)

    assert any(item.entry_signal_eligible for item in result.assessments)
    assert all(
        item.candidate_confirmation_count
        == int(item.score is not None and item.score > service.policy.effective_entry_score_hurdle)
        for item in result.assessments
    )
    assert all(
        item.entry_eligible_confirmation_count == int(item.entry_signal_eligible)
        for item in result.assessments
    )
    assert all("candidateConfirmationCount" in item for item in assessments)
    assert all("entryEligibleConfirmationCount" in item for item in assessments)
    assert all("entrySignalEligible" in item for item in assessments)


def test_v24_fee_adjusted_entry_hold_and_replacement_use_return_units(tmp_path) -> None:
    service, _, _ = _setup(tmp_path)
    service.policy = replace(
        dynamic_policy_for_version("dynamic-intraday-v2.4"),
        required_entry_confirmations=1,
        maximum_positions=1,
        minimum_expected_hold_return=-0.001,
    )
    template = CandidateAssessment("TESTKRW", True, "ELIGIBLE", None, Decimal("100"), Decimal("1"))
    too_weak = service._with_score_evidence(template, 0.65)  # noqa: SLF001
    entry = service._with_score_evidence(replace(template, pair="ENTRYKRW"), 0.75)  # noqa: SLF001
    incumbent = service._with_score_evidence(  # noqa: SLF001
        replace(template, pair="HELDKRW"), 0.45
    )
    challenger = service._with_score_evidence(  # noqa: SLF001
        replace(template, pair="CHALLENGERKRW"), 0.80
    )

    assert service._fee_adjusted_entry_return_passes(too_weak) is False  # noqa: SLF001
    assert service._fee_adjusted_entry_return_passes(entry) is True  # noqa: SLF001
    assert service._expected_hold_return_passes(incumbent) is True  # noqa: SLF001
    assert service._fee_adjusted_replacement_passes(challenger, incumbent) is True  # noqa: SLF001

    selected, _, _ = service._select_detailed(  # noqa: SLF001
        (incumbent, challenger),
        {"HELDKRW"},
        {},
        set(),
        confirmation_counts={},
    )
    assert {item.pair for item in selected} == {"CHALLENGERKRW"}


def test_v25_rejects_only_new_entries_that_remain_negative_over_four_hours(tmp_path) -> None:
    service, _, _ = _setup(tmp_path)
    service.policy = replace(
        dynamic_policy_for_version("dynamic-intraday-v2.5"),
        required_entry_confirmations=1,
    )
    candidate = service._with_score_evidence(  # noqa: SLF001
        CandidateAssessment(
            "TESTKRW",
            True,
            "ELIGIBLE",
            None,
            Decimal("100"),
            Decimal("1"),
            momentum_1h=0.0,
            momentum_4h=-0.001,
            momentum_24h=0.0,
            volatility=0.001,
        ),
        0.80,
    )

    selected, reasons, evidence = service._select_detailed(  # noqa: SLF001
        (candidate,), set(), {}, set(), confirmation_counts={}
    )

    assert selected == ()
    assert "NEW_ENTRY_BLOCKED_BY_4H_FALLING_KNIFE_GUARD" in reasons
    assert evidence["TESTKRW"] == ("NEW_ENTRY_BLOCKED_BY_4H_FALLING_KNIFE_GUARD",)


def test_v25_records_same_cohort_v24_rule_control_without_affecting_selection(tmp_path) -> None:
    service, _, _ = _setup(tmp_path)
    service.policy = replace(
        dynamic_policy_for_version("dynamic-intraday-v2.5"),
        required_entry_confirmations=1,
    )
    candidate = service._with_score_evidence(  # noqa: SLF001
        CandidateAssessment(
            "TESTKRW",
            True,
            "ELIGIBLE",
            None,
            Decimal("100"),
            Decimal("1"),
            momentum_1h=0.0,
            momentum_4h=-0.001,
            momentum_24h=0.0,
            volatility=0.001,
        ),
        0.80,
    )

    actual, _, _ = service._select_detailed(  # noqa: SLF001
        (candidate,), set(), {}, set(), confirmation_counts={}
    )
    variants = service._selection_variants(  # noqa: SLF001
        (candidate,),
        set(),
        {},
        set(),
        set(),
        {},
        set(),
        set(),
    )

    assert actual == ()
    assert len(variants) == 1
    assert variants[0].variant_id == V24_RULE_CONTROL_VARIANT_ID
    assert {item.pair for item in variants[0].selected} == {"TESTKRW"}


def test_v27_accuracy_patch_records_crowding_disagreement_and_abcd_shadow_variants(
    tmp_path,
) -> None:
    service, _, as_of = _setup(tmp_path)
    service.policy = dynamic_policy_for_version("dynamic-intraday-v2.7-accuracy-v1")
    candidate = service._with_score_evidence(  # noqa: SLF001
        CandidateAssessment(
            "TESTKRW",
            True,
            "ELIGIBLE",
            None,
            Decimal("100"),
            Decimal("1"),
            momentum_1h=0.0,
            momentum_4h=0.001,
            momentum_24h=0.0,
            volatility=0.001,
        ),
        0.75,
    )
    overlay = BtcDerivativesDecisionOverlay(
        decision_as_of=as_of,
        availability=DerivativesOverlayAvailability.AVAILABLE,
        recommendation=DerivativesOverlayRecommendation.NO_CONFIRMATION,
        reason_codes=("BASIS_INPUT_MARK_INDEX_PROXY",),
        snapshot_id="snapshot-v27-accuracy",
        snapshot_available_at=as_of,
        signal_as_of=as_of,
        age_seconds=0.0,
        feature_version="btc-squeeze-v3-mark-index-basis-proxy",
        state=SqueezeState.NONE,
        funding_rate=Decimal("0.0001"),
        basis_input_rate=Decimal("0.0001"),
        basis_input_source=BasisSource.MARK_INDEX_PROXY,
        global_long_short_ratio=Decimal("1.2"),
        liquidation_confirmed=True,
        crowding_availability=DerivativesOverlayAvailability.AVAILABLE,
        crowding_feature_version="btc-crowding-v1",
        crowding_state=CrowdingState.LONG_UNWIND,
        crowding_dominant_side=CrowdingSide.LONG,
        long_crowding_score=0.67,
        short_crowding_score=0.1,
        crowding_intensity=0.67,
        bullish_unwind_score=0.1,
        bearish_unwind_score=0.60,
        crowding_confidence=1.0,
        long_liquidation_usd_15m=1_000_000.0,
    )

    variants = service._selection_variants(  # noqa: SLF001
        (candidate,), set(), {}, set(), set(), {"TESTKRW": 2}, set(), set(), overlay
    )
    selected_by_variant = {
        item.variant_id: {selected.pair for selected in item.selected} for item in variants
    }
    diagnostics = service._decision_diagnostics(overlay)  # noqa: SLF001

    assert selected_by_variant[MOMENTUM_ONLY_VARIANT_ID] == {"TESTKRW"}
    assert selected_by_variant[RAW_DERIVATIVES_VARIANT_ID] == {"TESTKRW"}
    assert selected_by_variant[CROWDING_ONLY_VARIANT_ID] == set()
    assert selected_by_variant[PRODUCTION_GATE_VARIANT_ID] == set()
    assert diagnostics["currentCrowdingGate"] == {
        "blocked": True,
        "reason": "NEW_ENTRY_BLOCKED_BY_LONG_CROWDING_UNWIND",
    }
    assert diagnostics["policyCrowdingGate"]["blocked"] is False
    assert diagnostics["crowdingGateDisagreement"] is True


def test_v25_uses_proxy_basis_context_without_changing_spot_selection(tmp_path) -> None:
    service, repository, as_of = _setup(tmp_path)
    policy = replace(
        dynamic_policy_for_version("dynamic-intraday-v2.5"),
        required_entry_confirmations=1,
    )

    class OverlayProvider:
        def known_at(self, decision_at, *, feature_version, maximum_age):
            assert decision_at == as_of
            assert feature_version == "btc-squeeze-v3-mark-index-basis-proxy"
            assert maximum_age == timedelta(minutes=10)
            return BtcDerivativesDecisionOverlay(
                decision_as_of=decision_at,
                availability=DerivativesOverlayAvailability.AVAILABLE,
                recommendation=DerivativesOverlayRecommendation.NO_CONFIRMATION,
                reason_codes=("BASIS_INPUT_MARK_INDEX_PROXY",),
                snapshot_id="snapshot-v25",
                snapshot_available_at=decision_at,
                signal_as_of=decision_at,
                age_seconds=0.0,
                feature_version=feature_version,
                state=SqueezeState.NONE,
                liquidation_confirmed=False,
            )

    service = DynamicPaperRebalanceService(
        service._history,  # noqa: SLF001
        service._market_data,  # noqa: SLF001
        repository,
        PaperExchangeGatewayFactory(),
        policy,
        OverlayProvider(),
    )

    result = service.run(DynamicPaperRebalanceCommand("dynamic-paper", as_of))

    assert result.derivatives_overlay is not None
    assert [item.variant_id for item in result.selection_variants] == [V24_RULE_CONTROL_VARIANT_ID]
    assert result.derivatives_overlay.feature_version == "btc-squeeze-v3-mark-index-basis-proxy"
    assert "DERIVATIVES_SHADOW_AVAILABLE_NO_CONFIRMATION" in result.decision_reasons


def test_v26_derivatives_gate_blocks_only_new_entries_and_records_v25_control(tmp_path) -> None:
    service, _, as_of = _setup(tmp_path)
    service.policy = replace(
        dynamic_policy_for_version("dynamic-intraday-v2.6"),
        required_entry_confirmations=1,
    )
    candidate = service._with_score_evidence(  # noqa: SLF001
        CandidateAssessment(
            "TESTKRW",
            True,
            "ELIGIBLE",
            None,
            Decimal("100"),
            Decimal("1"),
            momentum_1h=0.0,
            momentum_4h=0.001,
            momentum_24h=0.0,
            volatility=0.001,
        ),
        0.75,
    )
    weak_derivatives = BtcDerivativesDecisionOverlay(
        decision_as_of=as_of,
        availability=DerivativesOverlayAvailability.AVAILABLE,
        recommendation=DerivativesOverlayRecommendation.NO_CONFIRMATION,
        reason_codes=("BASIS_INPUT_MARK_INDEX_PROXY",),
        snapshot_id="snapshot-v26",
        snapshot_available_at=as_of,
        signal_as_of=as_of,
        age_seconds=0.0,
        feature_version="btc-squeeze-v3-mark-index-basis-proxy",
        state=SqueezeState.NONE,
        funding_rate=Decimal("0.00001"),
        basis_input_rate=Decimal("-0.0002"),
        basis_input_source=BasisSource.MARK_INDEX_PROXY,
        global_long_short_ratio=Decimal("1.2"),
        liquidation_confirmed=False,
    )

    block_reason = service._derivatives_entry_block_reason(weak_derivatives)  # noqa: SLF001
    entry, reasons, evidence = service._select_detailed(  # noqa: SLF001
        (candidate,),
        set(),
        {},
        set(),
        confirmation_counts={},
        entry_block_reason=block_reason,
    )
    held, _, _ = service._select_detailed(  # noqa: SLF001
        (candidate,),
        {"TESTKRW"},
        {},
        set(),
        entry_block_reason=block_reason,
    )
    variants = service._selection_variants(  # noqa: SLF001
        (candidate,), set(), {}, set(), set(), {}, set(), set(), weak_derivatives
    )

    assert entry == ()
    assert block_reason in reasons
    assert evidence["TESTKRW"] == (block_reason,)
    assert {item.pair for item in held} == {"TESTKRW"}
    assert [item.variant_id for item in variants] == [V25_RULE_CONTROL_VARIANT_ID]
    assert {item.pair for item in variants[0].selected} == {"TESTKRW"}


def test_v27_combines_squeeze_and_crowding_gate_and_records_v26_control(tmp_path) -> None:
    service, _, as_of = _setup(tmp_path)
    service.policy = replace(
        dynamic_policy_for_version("dynamic-intraday-v2.7"),
        required_entry_confirmations=1,
    )
    candidate = service._with_score_evidence(  # noqa: SLF001
        CandidateAssessment(
            "TESTKRW",
            True,
            "ELIGIBLE",
            None,
            Decimal("100"),
            Decimal("1"),
            momentum_1h=0.0,
            momentum_4h=0.001,
            momentum_24h=0.0,
            volatility=0.001,
        ),
        0.75,
    )
    overlay = BtcDerivativesDecisionOverlay(
        decision_as_of=as_of,
        availability=DerivativesOverlayAvailability.AVAILABLE,
        recommendation=DerivativesOverlayRecommendation.NO_CONFIRMATION,
        reason_codes=("BASIS_INPUT_MARK_INDEX_PROXY",),
        snapshot_id="snapshot-v27",
        snapshot_available_at=as_of,
        signal_as_of=as_of,
        age_seconds=0.0,
        feature_version="btc-squeeze-v3-mark-index-basis-proxy",
        state=SqueezeState.NONE,
        funding_rate=Decimal("0.0001"),
        basis_input_rate=Decimal("0.0001"),
        basis_input_source=BasisSource.MARK_INDEX_PROXY,
        global_long_short_ratio=Decimal("1.2"),
        liquidation_confirmed=True,
        crowding_availability=DerivativesOverlayAvailability.AVAILABLE,
        crowding_feature_version="btc-crowding-v1",
        crowding_state=CrowdingState.LONG_UNWIND,
        crowding_dominant_side=CrowdingSide.LONG,
        long_crowding_score=0.9,
        short_crowding_score=0.1,
        crowding_intensity=0.9,
        bullish_unwind_score=0.1,
        bearish_unwind_score=0.8,
        crowding_confidence=1.0,
        long_liquidation_usd_15m=1_000_000.0,
    )

    block_reason = service._derivatives_entry_block_reason(overlay)  # noqa: SLF001
    entry, _, _ = service._select_detailed(  # noqa: SLF001
        (candidate,), set(), {}, set(), confirmation_counts={}, entry_block_reason=block_reason
    )
    held, _, _ = service._select_detailed(  # noqa: SLF001
        (candidate,), {"TESTKRW"}, {}, set(), entry_block_reason=block_reason
    )
    variants = service._selection_variants(  # noqa: SLF001
        (candidate,), set(), {}, set(), set(), {"TESTKRW": 2}, set(), set(), overlay
    )

    assert block_reason == "NEW_ENTRY_BLOCKED_BY_LONG_CROWDING_UNWIND"
    assert entry == ()
    assert {item.pair for item in held} == {"TESTKRW"}
    assert [item.variant_id for item in variants] == [V26_RULE_CONTROL_VARIANT_ID]
    assert {item.pair for item in variants[0].selected} == {"TESTKRW"}


def test_v24_selection_concentration_blocks_new_entry_but_not_existing_hold(tmp_path) -> None:
    service, repository, as_of = _setup(tmp_path)
    service.policy = replace(
        dynamic_policy_for_version("dynamic-intraday-v2.4"),
        required_entry_confirmations=1,
    )
    for index in range(20):
        decision_time = as_of - timedelta(minutes=15 * (20 - index))
        repository.save_rebalance_decision(
            PaperRebalanceDecisionRecord(
                f"concentration-{index}",
                "dynamic-paper",
                service.policy.strategy_version,
                decision_time,
                decision_time,
                False,
                Decimal("1000000"),
                "[]",
                '[{"pair":"BTCKRW"}]',
                "[]",
                (),
                (),
                "DRY_RUN",
                decision_time,
            )
        )

    blocked = service._selection_concentration_blocked(  # noqa: SLF001
        "dynamic-paper", as_of
    )
    candidate = service._with_score_evidence(  # noqa: SLF001
        CandidateAssessment("BTCKRW", True, "ELIGIBLE", None, Decimal("100"), Decimal("1")),
        0.80,
    )
    entry, reasons, _ = service._select_detailed(  # noqa: SLF001
        (candidate,), set(), {}, set(), confirmation_counts={}, concentration_blocked=blocked
    )
    held, _, _ = service._select_detailed(  # noqa: SLF001
        (candidate,), {"BTCKRW"}, {}, set(), concentration_blocked=blocked
    )

    assert blocked == {"BTCKRW"}
    assert entry == ()
    assert "NEW_ENTRY_BLOCKED_BY_SELECTION_CONCENTRATION" in reasons
    assert {item.pair for item in held} == {"BTCKRW"}


def test_v24_rolling_net_losses_block_another_buy_for_the_asset(tmp_path) -> None:
    service, repository, as_of = _setup(tmp_path)
    service.policy = dynamic_policy_for_version("dynamic-intraday-v2.4")
    with sqlite3.connect(repository.path) as connection:
        for index in range(2):
            executed_at = as_of - timedelta(hours=index + 1)
            connection.execute(
                """INSERT INTO paper_execution
                   (order_id, intent_id, portfolio_id, pair, side, quantity, price,
                    fee, realized_pnl, executed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"loss-order-{index}",
                    f"loss-intent-{index}",
                    "dynamic-paper",
                    "LOSSKRW",
                    "SELL",
                    "1",
                    "10000",
                    "10",
                    "-3000",
                    executed_at.isoformat(),
                ),
            )

    blocked = service._rolling_loss_blocked(  # noqa: SLF001
        "dynamic-paper", Decimal("1000000"), as_of
    )

    assert blocked == {"LOSSKRW"}


def test_v24_persists_point_in_time_derivatives_shadow_context(tmp_path) -> None:
    service, repository, as_of = _setup(tmp_path)
    policy = replace(
        dynamic_policy_for_version("dynamic-intraday-v2.4"),
        required_entry_confirmations=1,
    )

    class OverlayProvider:
        def known_at(self, decision_at, *, feature_version, maximum_age):
            assert decision_at == as_of
            assert feature_version == "btc-squeeze-v2-market-streams"
            assert maximum_age == timedelta(minutes=10)
            return BtcDerivativesDecisionOverlay(
                decision_as_of=decision_at,
                availability=DerivativesOverlayAvailability.AVAILABLE,
                recommendation=DerivativesOverlayRecommendation.CONFIRM_RISK_ON,
                reason_codes=("DERIVATIVES_RISK_ON_CONFIRMED",),
                snapshot_id="snapshot-v24",
                snapshot_available_at=decision_at,
                signal_as_of=decision_at,
                age_seconds=0.0,
                feature_version=feature_version,
                state=SqueezeState.ACTIVE,
                liquidation_confirmed=False,
            )

    service = DynamicPaperRebalanceService(
        service._history,  # noqa: SLF001
        service._market_data,  # noqa: SLF001
        repository,
        PaperExchangeGatewayFactory(),
        policy,
        OverlayProvider(),
    )

    result = service.run(DynamicPaperRebalanceCommand("dynamic-paper", as_of))
    decision = repository.list_rebalance_decisions("dynamic-paper")[0]
    context = json.loads(decision.market_context_json)

    assert result.derivatives_overlay is not None
    assert context["snapshotId"] == "snapshot-v24"
    assert context["recommendation"] == "CONFIRM_RISK_ON"
    assert "DERIVATIVES_SHADOW_AVAILABLE_CONFIRM_RISK_ON" in result.decision_reasons
