"""Narrow equal-weight portfolio construction with an explicit cash residual."""

from dataclasses import dataclass
from decimal import Decimal

from investment.crypto.domain.market import MarketRegime
from investment.crypto.domain.portfolio import (
    AssetAllocation,
    PortfolioAllocation,
    TradingPortfolio,
)
from investment.crypto.domain.signal import SignalDirection, StrategyResult


@dataclass(frozen=True, slots=True)
class EqualWeightPortfolioConstructor:
    maximum_positions: int = 3
    maximum_asset_weight: Decimal = Decimal("0.5")
    risk_on_invested_fraction: Decimal = Decimal("1")
    neutral_invested_fraction: Decimal = Decimal("0.5")

    def __post_init__(self) -> None:
        if self.maximum_positions <= 0:
            raise ValueError("maximum_positions must be positive")
        for value in (
            self.maximum_asset_weight,
            self.risk_on_invested_fraction,
            self.neutral_invested_fraction,
        ):
            if not Decimal("0") <= value <= Decimal("1"):
                raise ValueError("portfolio construction fractions must be within [0, 1]")

    def construct(
        self, portfolio: TradingPortfolio, strategy_result: StrategyResult
    ) -> PortfolioAllocation:
        selected = [
            signal
            for signal in strategy_result.signals
            if signal.direction is SignalDirection.LONG
        ][: self.maximum_positions]
        if strategy_result.regime is MarketRegime.RISK_OFF or not selected:
            return PortfolioAllocation(portfolio.portfolio_id, portfolio.purpose, (), Decimal("1"))
        regime_budget = (
            self.risk_on_invested_fraction
            if strategy_result.regime is MarketRegime.RISK_ON
            else self.neutral_invested_fraction
        )
        invested = min(
            regime_budget, self.maximum_asset_weight * Decimal(len(selected))
        )
        base_weight = invested / Decimal(len(selected))
        allocations = []
        allocated = Decimal("0")
        for index, signal in enumerate(selected):
            weight = invested - allocated if index == len(selected) - 1 else base_weight
            allocations.append(AssetAllocation(signal.asset, weight))
            allocated += weight
        return PortfolioAllocation(
            portfolio.portfolio_id,
            portfolio.purpose,
            tuple(allocations),
            Decimal("1") - invested,
        )
