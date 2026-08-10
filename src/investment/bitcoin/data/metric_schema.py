"""Shared vendor-neutral schema for Bitcoin intelligence metrics."""

import polars as pl

METRIC_SCHEMA = pl.Schema(
    {
        "dataset": pl.String(),
        "entity": pl.String(),
        "metric": pl.String(),
        "event_time": pl.Datetime("us", "UTC"),
        "available_at": pl.Datetime("us", "UTC"),
        "ingested_at": pl.Datetime("us", "UTC"),
        "valid_from": pl.Datetime("us", "UTC"),
        "value": pl.Float64(),
        "unit": pl.String(),
        "source": pl.String(),
        "revision": pl.Int64(),
    }
)

METRIC_COLUMNS = list(METRIC_SCHEMA)
