"""Read-only latest market overview for operations and user interfaces."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from investment.core.domain.observation import require_utc
from investment.crypto.application.backtest_service import build_universe
from investment.crypto.ports.market_data import CryptoMarketDataProvider


@dataclass(frozen=True, slots=True)
class LatestMarketPrice:
    pair: str
    as_of: datetime
    close: Decimal
    daily_change: float | None
    volume: Decimal


class CryptoMarketOverviewService:
    def __init__(self, provider: CryptoMarketDataProvider) -> None:
        self._provider = provider

    def latest(
        self, pair_symbols: tuple[str, ...], as_of: datetime
    ) -> tuple[LatestMarketPrice, ...]:
        cutoff = require_utc(as_of, "as_of")
        universe = build_universe(pair_symbols)
        bundle = self._provider.fetch(
            universe, cutoff - timedelta(days=14), cutoff + timedelta(days=1)
        )
        results = []
        for pair in universe.pairs:
            known = [
                candle for candle in bundle.candles[pair.symbol] if candle.available_at <= cutoff
            ]
            if not known:
                continue
            latest = known[-1]
            change = (
                float(latest.close / known[-2].close - Decimal("1")) if len(known) > 1 else None
            )
            results.append(
                LatestMarketPrice(
                    pair.symbol,
                    latest.available_at,
                    latest.close,
                    change,
                    latest.volume,
                )
            )
        return tuple(results)
