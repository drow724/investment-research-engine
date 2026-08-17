from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from investment.crypto.application.dynamic_paper_rebalance import (
    CandidateAssessment,
    DynamicPaperRebalanceCommand,
    DynamicPaperRebalanceService,
    DynamicUniversePolicy,
)
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
