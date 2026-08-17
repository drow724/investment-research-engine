"""Four-hour BTC regime derived only from completed 15-minute bars."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from investment.crypto.domain.market import (
    MarketDataBundle,
    MarketRegime,
    MarketRegimeResult,
)


@dataclass(frozen=True, slots=True)
class IntradayBtcRegimeModel:
    bars_per_regime_period: int = 16
    short_periods: int = 12
    long_periods: int = 42
    maximum_risk_on_volatility: float = 1.5

    def evaluate(self, market_data: MarketDataBundle, as_of: datetime) -> MarketRegimeResult:
        known = market_data.known_at(as_of)
        pair = next((item for item in known.universe.pairs if item.base.symbol == "BTC"), None)
        if pair is None:
            return MarketRegimeResult(MarketRegime.NEUTRAL, as_of, "btc_4h_trend_v1", {})
        candles = known.candles[pair.symbol]
        buckets = {}
        for candle in candles:
            bucket = candle.open_time.replace(
                hour=(candle.open_time.hour // 4) * 4, minute=0, second=0, microsecond=0
            )
            if bucket + timedelta(hours=4) <= as_of:
                buckets[bucket] = candle.close
        closes_4h = np.asarray([float(buckets[key]) for key in sorted(buckets)], dtype=np.float64)
        if len(closes_4h) < self.long_periods:
            return MarketRegimeResult(
                MarketRegime.NEUTRAL,
                as_of,
                "btc_4h_trend_v1",
                {"completed_4h_bars": float(len(closes_4h))},
            )
        latest = float(closes_4h[-1])
        short = float(np.mean(closes_4h[-self.short_periods :]))
        long = float(np.mean(closes_4h[-self.long_periods :]))
        returns = np.diff(np.log(closes_4h[-self.short_periods - 1 :]))
        volatility = float(np.std(returns, ddof=1) * np.sqrt(6 * 365)) if len(returns) > 1 else 0.0
        regime = (
            MarketRegime.RISK_ON
            if latest > long and short > long and volatility <= self.maximum_risk_on_volatility
            else MarketRegime.RISK_OFF
            if latest < long
            else MarketRegime.NEUTRAL
        )
        return MarketRegimeResult(
            regime,
            as_of,
            "btc_4h_trend_v1",
            {
                "btc_close": latest,
                "btc_4h_ma_short": short,
                "btc_4h_ma_long": long,
                "btc_4h_annualized_volatility": volatility,
            },
        )
