"""A transparent BTC trend/volatility regime baseline, not a final regime model."""

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from investment.crypto.domain.market import (
    MarketDataBundle,
    MarketRegime,
    MarketRegimeResult,
)


@dataclass(frozen=True, slots=True)
class BtcTrendRegimeModel:
    short_window: int = 50
    long_window: int = 200
    maximum_risk_on_volatility: float = 0.80

    def __post_init__(self) -> None:
        if self.short_window <= 1 or self.long_window <= self.short_window:
            raise ValueError("regime windows require 1 < short_window < long_window")
        if self.maximum_risk_on_volatility < 0:
            raise ValueError("volatility threshold cannot be negative")

    def evaluate(self, market_data: MarketDataBundle, as_of: datetime) -> MarketRegimeResult:
        known = market_data.known_at(as_of)
        btc_pair = next((pair for pair in known.universe.pairs if pair.base.symbol == "BTC"), None)
        if btc_pair is None:
            return MarketRegimeResult(MarketRegime.NEUTRAL, as_of, "btc_trend_v1", {})
        candles = known.candles[btc_pair.symbol]
        if len(candles) < self.long_window:
            return MarketRegimeResult(
                MarketRegime.NEUTRAL,
                as_of,
                "btc_trend_v1",
                {"observations": float(len(candles))},
            )
        closes = np.array([float(candle.close) for candle in candles], dtype=np.float64)
        latest = closes[-1]
        short_average = float(np.mean(closes[-self.short_window :]))
        long_average = float(np.mean(closes[-self.long_window :]))
        returns = np.diff(np.log(closes[-self.short_window - 1 :]))
        volatility = float(np.std(returns, ddof=1) * np.sqrt(365)) if len(returns) > 1 else 0.0
        if (
            latest > long_average
            and short_average > long_average
            and volatility <= self.maximum_risk_on_volatility
        ):
            regime = MarketRegime.RISK_ON
        elif latest < long_average:
            regime = MarketRegime.RISK_OFF
        else:
            regime = MarketRegime.NEUTRAL
        return MarketRegimeResult(
            regime,
            as_of,
            "btc_trend_v1",
            {
                "btc_close": latest,
                "btc_ma_short": short_average,
                "btc_ma_long": long_average,
                "btc_realized_volatility": volatility,
            },
        )
