from datetime import UTC, datetime, timedelta

from investment.crypto.application.intraday_service import (
    CryptoIntradayBacktestService,
    IntradayBacktestCommand,
)
from investment.crypto.domain.market import MarketCandle, MarketDataBundle
from investment.crypto.infrastructure.market_data import InMemoryCryptoMarketDataProvider
from tests.crypto_fixtures import crypto_bundle


def test_intraday_backtest_uses_15m_signal_and_hourly_rebalance() -> None:
    daily = crypto_bundle(800)
    origin = datetime(2025, 1, 1, tzinfo=UTC)
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
        for symbol, values in daily.candles.items()
    }
    bundle = MarketDataBundle(daily.universe, candles)
    service = CryptoIntradayBacktestService(InMemoryCryptoMarketDataProvider(bundle))

    result = service.run(
        IntradayBacktestCommand(
            ("BTC/KRW", "ETH/KRW", "SOL/KRW"),
            origin + timedelta(minutes=15 * 700),
            origin + timedelta(minutes=15 * 800),
        )
    )

    assert result.portfolio_id == "crypto-intraday-paper"
    assert result.rebalance_count == 25
    assert len(result.equity_curve) == 100
