"""Funding and open-interest features supported by the initial adapter."""

from dataclasses import dataclass

import polars as pl

from investment.bitcoin.features.metric import daily_metric, outer_join_series, rolling_zscore
from investment.core.data.point_in_time import PointInTimeDataset


@dataclass(frozen=True, slots=True)
class DerivativesFeatureFamily:
    zscore_window: int = 30

    def compute(self, dataset: PointInTimeDataset) -> pl.DataFrame:
        available = set(dataset.frame().get_column("metric").unique().to_list())
        frames: list[pl.DataFrame] = []
        if "funding_rate" in available:
            frames.append(daily_metric(dataset, "funding_rate", aggregation="mean"))
        if "open_interest" in available:
            frames.append(daily_metric(dataset, "open_interest", aggregation="last"))
        result = outer_join_series(frames)
        expressions: list[pl.Expr] = []
        if "funding_rate" in result.columns:
            expressions.append(
                rolling_zscore("funding_rate", self.zscore_window, "funding_zscore_30d")
            )
        if "open_interest" in result.columns:
            value = pl.col("open_interest")
            expressions.extend(
                [
                    (value / value.shift(1) - 1).alias("open_interest_change_1d"),
                    (value / value.shift(7) - 1).alias("open_interest_change_7d"),
                    rolling_zscore("open_interest", self.zscore_window, "open_interest_zscore"),
                ]
            )
        return result.with_columns(expressions) if expressions else result
