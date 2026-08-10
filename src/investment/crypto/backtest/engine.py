"""Reference long-only spot backtester using next-open execution."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import numpy as np

from investment.crypto.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PerformanceMetrics,
)
from investment.crypto.domain.market import MarketCandle, MarketDataBundle
from investment.crypto.domain.portfolio import TradingPortfolio
from investment.crypto.portfolio.equal_weight import EqualWeightPortfolioConstructor
from investment.crypto.ports.regime import MarketRegimeModel
from investment.crypto.ports.risk import AllocationRiskEngine
from investment.crypto.ports.strategy import Strategy, StrategyContext
from investment.crypto.ports.universe import UniverseEligibilityModel


@dataclass(frozen=True, slots=True)
class BacktestEngine:
    regime_model: MarketRegimeModel
    strategy: Strategy
    portfolio_constructor: EqualWeightPortfolioConstructor
    risk_engine: AllocationRiskEngine
    universe_eligibility: UniverseEligibilityModel | None = None

    def run(self, market_data: MarketDataBundle, config: BacktestConfig) -> BacktestResult:
        timeline = self._timeline(market_data, config.start, config.end)
        if len(timeline) < 2:
            raise ValueError("backtest requires at least two common trading timestamps")
        portfolio = TradingPortfolio(
            config.portfolio_id,
            config.purpose,
            market_data.universe.quote_asset,
            config.initial_capital,
        )
        equity = config.initial_capital
        fee_only_equity = config.initial_capital
        gross_equity = config.initial_capital
        weights: dict[str, Decimal] = {}
        total_turnover = Decimal("0")
        total_fee_impact = Decimal("0")
        total_slippage_impact = Decimal("0")
        rejected = 0
        rebalances = 0
        daily_returns: list[float] = []
        curve = [EquityPoint(timeline[0], equity, gross_equity, Decimal("1"))]

        lookup = self._candle_lookup(market_data)
        for index, timestamp in enumerate(timeline[:-1]):
            if index % config.rebalance_days == 0:
                regime = self.regime_model.evaluate(market_data, timestamp)
                eligibility = (
                    self.universe_eligibility.evaluate(market_data, timestamp)
                    if self.universe_eligibility is not None
                    else None
                )
                strategy_result = self.strategy.generate(
                    StrategyContext(
                        timestamp,
                        market_data,
                        regime,
                        (
                            tuple(pair.base for pair in eligibility.eligible_pairs)
                            if eligibility is not None
                            else None
                        ),
                    )
                )
                proposal = self.portfolio_constructor.construct(portfolio, strategy_result)
                decision = self.risk_engine.evaluate_allocation(proposal, portfolio)
                if decision.approved:
                    new_weights = {
                        allocation.asset.symbol: allocation.weight
                        for allocation in proposal.allocations
                    }
                    turnover = sum(
                        (
                            abs(
                                new_weights.get(asset, Decimal("0"))
                                - weights.get(asset, Decimal("0"))
                            )
                            for asset in set(new_weights) | set(weights)
                        ),
                        Decimal("0"),
                    )
                    fee_impact = turnover * config.fee_rate
                    slippage_impact = turnover * config.slippage_rate
                    equity *= Decimal("1") - fee_impact - slippage_impact
                    fee_only_equity *= Decimal("1") - fee_impact
                    total_fee_impact += fee_impact
                    total_slippage_impact += slippage_impact
                    total_turnover += turnover
                    weights = new_weights
                    rebalances += 1
                else:
                    rejected += 1

            next_timestamp = timeline[index + 1]
            portfolio_return = Decimal("0")
            for asset, weight in weights.items():
                pair = market_data.universe.pair_for(
                    next(item for item in market_data.universe.assets if item.symbol == asset)
                )
                current = lookup[pair.symbol][timestamp]
                following = lookup[pair.symbol][next_timestamp]
                portfolio_return += weight * (following.open / current.open - Decimal("1"))
            equity *= Decimal("1") + portfolio_return
            fee_only_equity *= Decimal("1") + portfolio_return
            gross_equity *= Decimal("1") + portfolio_return
            daily_returns.append(float(portfolio_return))
            cash_weight = Decimal("1") - sum(weights.values(), Decimal("0"))
            curve.append(EquityPoint(next_timestamp, equity, gross_equity, cash_weight))

        metrics = self._metrics(
            market_data,
            timeline,
            curve,
            daily_returns,
            config,
            fee_only_equity,
            total_turnover,
            total_fee_impact,
            total_slippage_impact,
            lookup,
        )
        return BacktestResult(
            self.strategy.name,
            self.strategy.version,
            config.portfolio_id,
            config.purpose,
            timeline[0],
            timeline[-1],
            metrics,
            tuple(curve),
            rebalances,
            rejected,
        )

    @staticmethod
    def _timeline(
        market_data: MarketDataBundle, start: datetime, end: datetime
    ) -> list[datetime]:
        timestamps = [
            {candle.open_time for candle in market_data.candles[pair.symbol]}
            for pair in market_data.universe.pairs
        ]
        common = set.intersection(*timestamps)
        return sorted(value for value in common if start <= value < end)

    @staticmethod
    def _candle_lookup(
        market_data: MarketDataBundle,
    ) -> dict[str, dict[datetime, MarketCandle]]:
        return {
            symbol: {candle.open_time: candle for candle in candles}
            for symbol, candles in market_data.candles.items()
        }

    def _metrics(
        self,
        market_data: MarketDataBundle,
        timeline: list[datetime],
        curve: list[EquityPoint],
        returns: list[float],
        config: BacktestConfig,
        fee_only_equity: Decimal,
        turnover: Decimal,
        fee_impact: Decimal,
        slippage_impact: Decimal,
        lookup: dict[str, dict[datetime, MarketCandle]],
    ) -> PerformanceMetrics:
        initial = config.initial_capital
        total_return = float(curve[-1].equity / initial - Decimal("1"))
        gross_return = float(curve[-1].gross_equity / initial - Decimal("1"))
        fee_adjusted = float(fee_only_equity / initial - Decimal("1"))
        periods_per_year = 365.0
        years = max((timeline[-1] - timeline[0]).total_seconds() / (365.25 * 86400), 1 / 365.25)
        cagr = float(curve[-1].equity / initial) ** (1 / years) - 1
        array = np.asarray(returns, dtype=np.float64)
        volatility = (
            float(np.std(array, ddof=1) * np.sqrt(periods_per_year))
            if len(array) > 1
            else 0.0
        )
        mean = float(np.mean(array)) if len(array) else 0.0
        std = float(np.std(array, ddof=1)) if len(array) > 1 else 0.0
        downside = array[array < 0]
        downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 0.0
        sharpe = mean / std * np.sqrt(periods_per_year) if std > 0 else None
        sortino = mean / downside_std * np.sqrt(periods_per_year) if downside_std > 0 else None
        equity_values = np.array([float(point.equity) for point in curve])
        drawdowns = equity_values / np.maximum.accumulate(equity_values) - 1
        hit_rate = float(np.mean(array > 0)) if len(array) else 0.0
        benchmark_pair = next(
            (pair for pair in market_data.universe.pairs if pair.base.symbol == "BTC"),
            market_data.universe.pairs[0],
        )
        first = lookup[benchmark_pair.symbol][timeline[0]]
        last = lookup[benchmark_pair.symbol][timeline[-1]]
        buy_hold = float(last.open / first.open - Decimal("1"))
        return PerformanceMetrics(
            total_return=total_return,
            gross_total_return=gross_return,
            fee_adjusted_return=fee_adjusted,
            slippage_adjusted_return=total_return,
            cagr=cagr,
            sharpe_ratio=float(sharpe) if sharpe is not None else None,
            sortino_ratio=float(sortino) if sortino is not None else None,
            maximum_drawdown=float(np.min(drawdowns)),
            volatility=volatility,
            hit_rate=hit_rate,
            turnover=float(turnover),
            total_fee_impact=float(fee_impact),
            total_slippage_impact=float(slippage_impact),
            buy_and_hold_return=buy_hold,
            cash_benchmark_return=0.0,
        )
