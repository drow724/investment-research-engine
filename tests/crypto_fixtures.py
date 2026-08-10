from datetime import UTC, datetime, timedelta
from decimal import Decimal

from investment.crypto.domain.market import (
    Asset,
    AssetKind,
    MarketCandle,
    MarketDataBundle,
    TradingPair,
    TradingUniverse,
)


def crypto_bundle(days: int = 320) -> MarketDataBundle:
    cash = Asset("KRW", AssetKind.CASH)
    pairs = tuple(TradingPair(Asset(symbol), cash) for symbol in ("BTC", "ETH", "SOL"))
    universe = TradingUniverse(pairs)
    start = datetime(2023, 1, 1, tzinfo=UTC)
    slopes = {"BTC": Decimal("100"), "ETH": Decimal("80"), "SOL": Decimal("150")}
    bases = {"BTC": Decimal("30000000"), "ETH": Decimal("2000000"), "SOL": Decimal("30000")}
    candles = {}
    for pair in pairs:
        values = []
        for index in range(days):
            price = bases[pair.base.symbol] + slopes[pair.base.symbol] * index
            opened = start + timedelta(days=index)
            values.append(
                MarketCandle(
                    pair=pair,
                    open_time=opened,
                    available_at=opened + timedelta(hours=23, minutes=59),
                    open=price,
                    high=price * Decimal("1.01"),
                    low=price * Decimal("0.99"),
                    close=price + slopes[pair.base.symbol] / Decimal("2"),
                    volume=Decimal("1000000"),
                )
            )
        candles[pair.symbol] = tuple(values)
    return MarketDataBundle(universe, candles)
