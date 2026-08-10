"""In-memory and normalized-Parquet crypto market-data providers."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import polars as pl

from investment.core.domain.observation import require_utc
from investment.crypto.domain.market import (
    MarketCandle,
    MarketDataBundle,
    TradingPair,
    TradingUniverse,
)


class InMemoryCryptoMarketDataProvider:
    def __init__(self, bundle: MarketDataBundle) -> None:
        self._bundle = bundle

    def fetch(
        self, universe: TradingUniverse, start: datetime, end: datetime
    ) -> MarketDataBundle:
        start_utc = require_utc(start, "start")
        end_utc = require_utc(end, "end")
        if universe != self._bundle.universe:
            raise ValueError("requested universe does not match in-memory data")
        candles = {
            symbol: tuple(
                candle
                for candle in values
                if start_utc <= candle.open_time < end_utc
            )
            for symbol, values in self._bundle.candles.items()
        }
        return MarketDataBundle(universe, candles)


class ParquetCryptoMarketDataProvider:
    """Read one normalized daily OHLCV Parquet file per pair symbol."""

    def __init__(self, root: str | Path = "data/normalized/crypto/price") -> None:
        self.root = Path(root)

    def fetch(
        self, universe: TradingUniverse, start: datetime, end: datetime
    ) -> MarketDataBundle:
        start_utc = require_utc(start, "start")
        end_utc = require_utc(end, "end")
        candles = {
            pair.symbol: self._read_pair(pair, start_utc, end_utc)
            for pair in universe.pairs
        }
        return MarketDataBundle(universe, candles)

    def _read_pair(
        self, pair: TradingPair, start: datetime, end: datetime
    ) -> tuple[MarketCandle, ...]:
        path = self.root / f"{pair.symbol}.parquet"
        frame = pl.read_parquet(path).filter(
            (pl.col("open_time") >= start) & (pl.col("open_time") < end)
        ).sort("open_time")
        required = {"open_time", "available_at", "open", "high", "low", "close", "volume"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"missing crypto OHLCV columns: {sorted(missing)}")
        return tuple(
            MarketCandle(
                pair,
                row["open_time"],
                row["available_at"],
                Decimal(str(row["open"])),
                Decimal(str(row["high"])),
                Decimal(str(row["low"])),
                Decimal(str(row["close"])),
                Decimal(str(row["volume"])),
            )
            for row in frame.iter_rows(named=True)
        )
