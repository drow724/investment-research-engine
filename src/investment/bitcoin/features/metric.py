"""Vendor-neutral transformations over revision-resolved long-form metrics."""

from typing import Literal

import polars as pl

from investment.core.data.point_in_time import PointInTimeDataset


def daily_metric(
    dataset: PointInTimeDataset,
    metric: str,
    *,
    aggregation: Literal["sum", "mean", "last"] = "mean",
) -> pl.DataFrame:
    frame = dataset.frame().filter(pl.col("metric") == metric)
    if frame.is_empty():
        return pl.DataFrame(schema={"open_time": pl.Datetime("us", "UTC"), metric: pl.Float64()})
    # Align the observation to when it was knowable, not merely to the economic event date.
    frame = frame.with_columns(pl.col("available_at").dt.truncate("1d").alias("open_time"))
    value = pl.col("value")
    expression = {
        "sum": value.sum(),
        "mean": value.mean(),
        "last": value.last(),
    }[aggregation]
    return frame.group_by("open_time").agg(expression.alias(metric)).sort("open_time")


def rolling_zscore(column: str, days: int, name: str | None = None) -> pl.Expr:
    value = pl.col(column)
    mean = value.rolling_mean(days)
    std = value.rolling_std(days)
    return ((value - mean) / std).alias(name or f"{column}_zscore_{days}d")


def outer_join_series(frames: list[pl.DataFrame]) -> pl.DataFrame:
    if not frames:
        return pl.DataFrame(schema={"open_time": pl.Datetime("us", "UTC")})
    result = frames[0]
    for frame in frames[1:]:
        result = result.join(frame, on="open_time", how="full", coalesce=True)
    return result.sort("open_time")
