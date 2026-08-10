"""LTH and exchange metrics without directional investment claims."""

from dataclasses import dataclass

import polars as pl

from investment.bitcoin.features.metric import daily_metric, outer_join_series, rolling_zscore
from investment.core.data.point_in_time import PointInTimeDataset


@dataclass(frozen=True, slots=True)
class HolderFeatureFamily:
    zscore_window: int = 30

    def compute(self, dataset: PointInTimeDataset) -> pl.DataFrame:
        available = set(dataset.frame().get_column("metric").unique().to_list())
        frames: list[pl.DataFrame] = []
        for metric in (
            "lth_supply",
            "lth_spending",
            "lth_realized_profit",
            "lth_realized_loss",
            "exchange_inflow",
            "exchange_outflow",
            "exchange_netflow",
        ):
            if metric in available:
                frames.append(daily_metric(dataset, metric, aggregation="mean"))
        result = outer_join_series(frames)
        if "lth_supply" in result.columns:
            result = result.with_columns(
                (pl.col("lth_supply") / pl.col("lth_supply").shift(7) - 1).alias(
                    "lth_supply_change_7d"
                ),
                (pl.col("lth_supply") / pl.col("lth_supply").shift(30) - 1).alias(
                    "lth_supply_change_30d"
                ),
            )
        zscore_names = {
            "lth_spending": "lth_spending_zscore_30d",
            "lth_realized_profit": "lth_realized_profit_zscore",
            "lth_realized_loss": "lth_realized_loss_zscore",
            "exchange_inflow": "exchange_inflow_zscore",
            "exchange_outflow": "exchange_outflow_zscore",
            "exchange_netflow": "exchange_netflow_zscore",
        }
        expressions = [
            rolling_zscore(metric, self.zscore_window, name)
            for metric, name in zscore_names.items()
            if metric in result.columns
        ]
        return result.with_columns(expressions) if expressions else result
