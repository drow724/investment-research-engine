"""Point-in-time cross-sectional features and executable-open forward labels."""

import hashlib
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import numpy as np
import polars as pl

from investment.core.domain.observation import require_utc
from investment.crypto.domain.market import MarketCandle, MarketDataBundle
from investment.crypto.ports.universe import UniverseEligibilityModel

FEATURE_COLUMNS = (
    "momentum_7d",
    "momentum_30d",
    "momentum_90d",
    "relative_strength_30d",
    "realized_volatility_30d",
    "drawdown_30d",
    "drawdown_90d",
    "volume_zscore_30d",
    "log_average_quote_volume_30d",
    "btc_correlation_30d",
    "market_regime_score",
)


class UniverseMode(StrEnum):
    POINT_IN_TIME = "POINT_IN_TIME"
    STATIC_EXPLICIT = "STATIC_EXPLICIT"


@dataclass(frozen=True, slots=True)
class MLDatasetMetadata:
    universe_mode: UniverseMode
    feature_version: str
    label_horizon_days: int
    start: datetime
    end: datetime
    row_count: int
    content_hash: str
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MLDataset:
    frame: pl.DataFrame
    metadata: MLDatasetMetadata


class CrossSectionalDatasetBuilder:
    feature_version = "crypto_cross_sectional_v1"
    maximum_feature_lookback = 200

    def __init__(
        self,
        universe_mode: UniverseMode = UniverseMode.POINT_IN_TIME,
        eligibility_model: UniverseEligibilityModel | None = None,
    ) -> None:
        if universe_mode is UniverseMode.POINT_IN_TIME and eligibility_model is None:
            raise ValueError("POINT_IN_TIME mode requires an eligibility model")
        self.universe_mode = universe_mode
        self.eligibility_model = eligibility_model

    def build(
        self,
        market_data: MarketDataBundle,
        start: datetime,
        end: datetime,
        *,
        label_horizon_days: int,
    ) -> MLDataset:
        start_utc = require_utc(start, "start")
        end_utc = require_utc(end, "end")
        if start_utc >= end_utc or label_horizon_days <= 0:
            raise ValueError("invalid dataset range or label horizon")
        timeline = _common_timeline(market_data)
        positions = {timestamp: index for index, timestamp in enumerate(timeline)}
        candle_map = {
            symbol: {candle.open_time: candle for candle in candles}
            for symbol, candles in market_data.candles.items()
        }
        available_times = {
            symbol: [candle.available_at for candle in candles]
            for symbol, candles in market_data.candles.items()
        }
        btc_symbol = next(
            (
                pair.symbol
                for pair in market_data.universe.pairs
                if pair.base.symbol == "BTC"
            ),
            None,
        )
        if btc_symbol is None:
            raise ValueError("BTC must be present as the market reference asset")
        rows: list[dict[str, object]] = []
        for as_of in timeline:
            if not start_utc <= as_of < end_utc:
                continue
            future_index = positions[as_of] + label_horizon_days
            if future_index >= len(timeline):
                continue
            eligible = self._eligible_symbols(market_data, as_of)
            btc_known = _known_candles(
                market_data.candles[btc_symbol], available_times[btc_symbol], as_of
            )
            if len(btc_known) < self.maximum_feature_lookback:
                continue
            for pair in market_data.universe.pairs:
                if pair.symbol not in eligible:
                    continue
                known = _known_candles(
                    market_data.candles[pair.symbol], available_times[pair.symbol], as_of
                )
                if len(known) < self.maximum_feature_lookback:
                    continue
                entry = candle_map[pair.symbol].get(as_of)
                exit_candle = candle_map[pair.symbol].get(timeline[future_index])
                if entry is None or exit_candle is None:
                    continue
                features = _features(known, btc_known)
                if features is None:
                    continue
                rows.append(
                    {
                        "as_of": as_of,
                        "asset": pair.base.symbol,
                        **features,
                        "forward_return": float(exit_candle.open / entry.open - 1),
                    }
                )
        frame = (
            pl.DataFrame(rows).sort("as_of", "asset")
            if rows
            else _empty_dataset_frame()
        )
        content_hash = _frame_hash(frame)
        limitations = (
            ("STATIC_UNIVERSE_SURVIVORSHIP_RISK",)
            if self.universe_mode is UniverseMode.STATIC_EXPLICIT
            else ()
        )
        return MLDataset(
            frame,
            MLDatasetMetadata(
                self.universe_mode,
                self.feature_version,
                label_horizon_days,
                start_utc,
                end_utc,
                frame.height,
                content_hash,
                limitations,
            ),
        )

    def build_current_features(
        self, market_data: MarketDataBundle, as_of: datetime
    ) -> pl.DataFrame:
        cutoff = require_utc(as_of, "as_of")
        available_times = {
            symbol: [candle.available_at for candle in candles]
            for symbol, candles in market_data.candles.items()
        }
        btc_symbol = next(
            pair.symbol
            for pair in market_data.universe.pairs
            if pair.base.symbol == "BTC"
        )
        btc_known = _known_candles(
            market_data.candles[btc_symbol], available_times[btc_symbol], cutoff
        )
        if len(btc_known) < self.maximum_feature_lookback:
            return _empty_feature_frame()
        eligible = self._eligible_symbols(market_data, cutoff)
        rows = []
        for pair in market_data.universe.pairs:
            if pair.symbol not in eligible:
                continue
            known = _known_candles(
                market_data.candles[pair.symbol], available_times[pair.symbol], cutoff
            )
            if len(known) < self.maximum_feature_lookback:
                continue
            features = _features(known, btc_known)
            if features is not None:
                rows.append({"as_of": cutoff, "asset": pair.base.symbol, **features})
        return pl.DataFrame(rows).sort("asset") if rows else _empty_feature_frame()

    def _eligible_symbols(
        self, market_data: MarketDataBundle, as_of: datetime
    ) -> set[str]:
        if self.universe_mode is UniverseMode.STATIC_EXPLICIT:
            return {pair.symbol for pair in market_data.universe.pairs}
        if self.eligibility_model is None:
            raise AssertionError("POINT_IN_TIME eligibility was validated at construction")
        result = self.eligibility_model.evaluate(market_data, as_of)
        return {pair.symbol for pair in result.eligible_pairs}


def _known_candles(
    candles: tuple[MarketCandle, ...], available_times: list[datetime], as_of: datetime
) -> tuple[MarketCandle, ...]:
    return candles[: bisect_right(available_times, as_of)]


def _features(
    candles: tuple[MarketCandle, ...], btc_candles: tuple[MarketCandle, ...]
) -> dict[str, float] | None:
    closes = np.asarray([float(candle.close) for candle in candles], dtype=np.float64)
    volumes = np.asarray([float(candle.volume) for candle in candles], dtype=np.float64)
    btc_closes = np.asarray([float(candle.close) for candle in btc_candles], dtype=np.float64)
    if len(closes) < 200 or len(btc_closes) < 200:
        return None
    returns_30 = np.diff(np.log(closes[-31:]))
    btc_returns_30 = np.diff(np.log(btc_closes[-31:]))
    correlation = float(np.corrcoef(returns_30, btc_returns_30)[0, 1])
    if not np.isfinite(correlation):
        correlation = 0.0
    volume_window = volumes[-30:]
    volume_std = float(np.std(volume_window, ddof=1))
    volume_zscore = (
        float((volumes[-1] - np.mean(volume_window)) / volume_std)
        if volume_std > 0
        else 0.0
    )
    quote_volume = np.mean(closes[-30:] * volumes[-30:])
    btc_short = float(np.mean(btc_closes[-50:]))
    btc_long = float(np.mean(btc_closes[-200:]))
    regime_score = 1.0 if btc_closes[-1] > btc_long and btc_short > btc_long else (
        -1.0 if btc_closes[-1] < btc_long else 0.0
    )
    return {
        "momentum_7d": closes[-1] / closes[-8] - 1,
        "momentum_30d": closes[-1] / closes[-31] - 1,
        "momentum_90d": closes[-1] / closes[-91] - 1,
        "relative_strength_30d": (closes[-1] / closes[-31])
        - (btc_closes[-1] / btc_closes[-31]),
        "realized_volatility_30d": float(np.std(returns_30, ddof=1) * np.sqrt(365)),
        "drawdown_30d": closes[-1] / np.max(closes[-30:]) - 1,
        "drawdown_90d": closes[-1] / np.max(closes[-90:]) - 1,
        "volume_zscore_30d": volume_zscore,
        "log_average_quote_volume_30d": float(np.log1p(quote_volume)),
        "btc_correlation_30d": correlation,
        "market_regime_score": regime_score,
    }


def _common_timeline(market_data: MarketDataBundle) -> list[datetime]:
    sets = [
        {candle.open_time for candle in market_data.candles[pair.symbol]}
        for pair in market_data.universe.pairs
    ]
    return sorted(set.intersection(*sets))


def _frame_hash(frame: pl.DataFrame) -> str:
    content = frame.hash_rows(seed=0).to_numpy().tobytes()
    return hashlib.sha256(repr(frame.schema).encode() + content).hexdigest()


def _empty_dataset_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "as_of": pl.Datetime("us", "UTC"),
            "asset": pl.String,
            **{name: pl.Float64 for name in FEATURE_COLUMNS},
            "forward_return": pl.Float64,
        }
    )


def _empty_feature_frame() -> pl.DataFrame:
    return _empty_dataset_frame().drop("forward_return")
