from datetime import timedelta
from decimal import Decimal

from investment.crypto.application.backtest_service import (
    CryptoBacktestService,
    MomentumBacktestCommand,
)
from investment.crypto.domain.portfolio import PortfolioPurpose
from investment.crypto.infrastructure.market_data import InMemoryCryptoMarketDataProvider
from tests.crypto_fixtures import crypto_bundle


def test_reference_vertical_slice_runs_with_costs_and_benchmarks() -> None:
    bundle = crypto_bundle()
    first = bundle.candles["BTCKRW"][220].open_time
    end = bundle.candles["BTCKRW"][300].open_time
    result = CryptoBacktestService(InMemoryCryptoMarketDataProvider(bundle)).run_momentum(
        MomentumBacktestCommand(
            pair_symbols=("BTC/KRW", "ETH/KRW", "SOL/KRW"),
            start=first,
            end=end,
            initial_capital=Decimal("100000"),
            purpose=PortfolioPurpose.PAPER_TRADING,
            lookback_days=30,
            maximum_positions=3,
            rebalance_days=7,
        )
    )
    assert result.strategy_name == "cross_sectional_momentum"
    assert result.purpose is PortfolioPurpose.PAPER_TRADING
    assert result.rebalance_count > 0
    assert result.metrics.gross_total_return > result.metrics.total_return
    assert result.metrics.turnover > 0
    assert result.metrics.cash_benchmark_return == 0
    assert len(result.equity_curve) > 2
    # The first signal at a day's open can see only candles published before that open.
    assert bundle.candles["BTCKRW"][220].available_at > first
    assert result.end < end + timedelta(days=1)
