from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investment.crypto.domain.market import Asset, AssetKind, MarketRegime
from investment.crypto.domain.portfolio import (
    AssetAllocation,
    PortfolioAllocation,
    PortfolioPurpose,
    TradingPortfolio,
)
from investment.crypto.domain.signal import Signal, SignalDirection, StrategyResult
from investment.crypto.portfolio.equal_weight import EqualWeightPortfolioConstructor
from investment.crypto.risk.engine import DeterministicRiskEngine, RiskPolicy


def test_core_investment_portfolio_cannot_enter_crypto_trading_context() -> None:
    cash = Asset("KRW", AssetKind.CASH)
    with pytest.raises(ValueError, match="core investment"):
        TradingPortfolio("core-btc", PortfolioPurpose.CORE_INVESTMENT, cash, Decimal("1"))
    with pytest.raises(ValueError, match="core investment"):
        PortfolioAllocation("core-btc", PortfolioPurpose.CORE_INVESTMENT, (), Decimal("1"))


def test_universe_can_be_wide_while_constructed_portfolio_is_narrow() -> None:
    cash = Asset("KRW", AssetKind.CASH)
    portfolio = TradingPortfolio("paper", PortfolioPurpose.PAPER_TRADING, cash, Decimal("100000"))
    now = datetime(2025, 1, 1, tzinfo=UTC)
    signals = tuple(
        Signal(Asset(symbol), SignalDirection.LONG, Decimal("1"), now, "fixture")
        for symbol in ("BTC", "ETH", "SOL", "XRP", "DOGE")
    )
    result = StrategyResult("fixture", "v1", now, MarketRegime.RISK_ON, signals)
    allocation = EqualWeightPortfolioConstructor(maximum_positions=3).construct(portfolio, result)
    assert len(allocation.allocations) == 3
    assert allocation.cash_weight == 0
    assert sum((item.weight for item in allocation.allocations), Decimal("0")) == 1


def test_risk_engine_is_independent_and_can_reject_strategy_allocation() -> None:
    cash = Asset("KRW", AssetKind.CASH)
    btc = Asset("BTC")
    portfolio = TradingPortfolio("paper", PortfolioPurpose.PAPER_TRADING, cash, Decimal("100000"))
    proposal = PortfolioAllocation(
        "paper",
        PortfolioPurpose.PAPER_TRADING,
        (AssetAllocation(btc, Decimal("0.8")),),
        Decimal("0.2"),
    )
    decision = DeterministicRiskEngine(
        RiskPolicy(maximum_asset_fraction=Decimal("0.5"))
    ).evaluate_allocation(proposal, portfolio)
    assert not decision.approved
    assert "MAXIMUM_ASSET_ALLOCATION_EXCEEDED" in decision.violations


def test_single_signal_keeps_required_cash_instead_of_breaching_asset_limit() -> None:
    cash = Asset("KRW", AssetKind.CASH)
    portfolio = TradingPortfolio("paper", PortfolioPurpose.PAPER_TRADING, cash, Decimal("100000"))
    now = datetime(2025, 1, 1, tzinfo=UTC)
    result = StrategyResult(
        "fixture",
        "v1",
        now,
        MarketRegime.RISK_ON,
        (Signal(Asset("BTC"), SignalDirection.LONG, Decimal("1"), now, "fixture"),),
    )
    allocation = EqualWeightPortfolioConstructor().construct(portfolio, result)
    assert allocation.allocations[0].weight == Decimal("0.5")
    assert allocation.cash_weight == Decimal("0.5")
