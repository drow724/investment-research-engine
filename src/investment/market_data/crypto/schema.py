"""Canonical normalized crypto OHLCV schema."""

import polars as pl

OHLCV_SCHEMA = pl.Schema(
    {
        "symbol": pl.String(),
        "open_time": pl.Datetime("ms", "UTC"),
        "available_at": pl.Datetime("ms", "UTC"),
        "ingested_at": pl.Datetime("us", "UTC"),
        "open": pl.Float64(),
        "high": pl.Float64(),
        "low": pl.Float64(),
        "close": pl.Float64(),
        "volume": pl.Float64(),
        "source": pl.String(),
    }
)

OHLCV_COLUMNS = list(OHLCV_SCHEMA)
