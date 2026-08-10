from decimal import Decimal

from investment.crypto.application.backtest_service import (
    CryptoBacktestService,
    MomentumBacktestCommand,
)
from investment.crypto.domain.universe import UniverseHistory
from investment.crypto.infrastructure.market_data import InMemoryCryptoMarketDataProvider
from investment.crypto.universe.liquidity import PointInTimeLiquidityUniverse
from tests.crypto_fixtures import crypto_bundle


def test_backtest_does_not_invent_membership_before_first_snapshot() -> None:
    bundle = crypto_bundle()
    start = bundle.candles["BTCKRW"][220].open_time
    end = bundle.candles["BTCKRW"][260].open_time
    eligibility = PointInTimeLiquidityUniverse(
        UniverseHistory(()), minimum_average_quote_volume=Decimal("0")
    )
    service = CryptoBacktestService(
        InMemoryCryptoMarketDataProvider(bundle), eligibility
    )
    result = service.run_momentum(
        MomentumBacktestCommand(
            ("BTC/KRW", "ETH/KRW", "SOL/KRW"),
            start,
            end,
            Decimal("100000"),
        )
    )
    assert result.metrics.total_return == 0
    assert all(point.cash_weight == 1 for point in result.equity_curve)
