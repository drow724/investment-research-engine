"""Post-2024 spot Bitcoin ETF flow features."""

from dataclasses import dataclass

import polars as pl

from investment.bitcoin.features.metric import rolling_zscore
from investment.core.data.point_in_time import PointInTimeDataset


@dataclass(frozen=True, slots=True)
class EtfFlowFeatureFamily:
    short_window: int = 5
    medium_window: int = 20
    long_window: int = 60

    def compute(
        self, dataset: PointInTimeDataset, market_cap: pl.DataFrame | None = None
    ) -> pl.DataFrame:
        raw = (
            dataset.frame()
            .filter(pl.col("metric") == "etf_net_flow")
            .with_columns(pl.col("available_at").dt.truncate("1d").alias("open_time"))
        )
        if raw.is_empty():
            raise ValueError("etf_net_flow metric is unavailable")
        aggregate = (
            raw.filter(pl.col("entity") == "aggregate")
            .group_by("open_time")
            .agg(pl.col("value").last().alias("_aggregate"))
        )
        individual = (
            raw.filter(pl.col("entity") != "aggregate")
            .group_by("open_time")
            .agg(pl.col("value").sum().alias("_individual"))
        )
        frame = aggregate.join(individual, on="open_time", how="full", coalesce=True).sort(
            "open_time"
        )
        frame = frame.with_columns(
            pl.coalesce("_aggregate", "_individual").alias("etf_net_flow_1d")
        ).drop("_aggregate", "_individual")
        flow = pl.col("etf_net_flow_1d")
        frame = frame.with_columns(
            flow.rolling_sum(self.short_window).alias("etf_net_flow_5d_sum"),
            flow.rolling_sum(self.medium_window).alias("etf_net_flow_20d_sum"),
            rolling_zscore("etf_net_flow_1d", self.medium_window, "etf_flow_zscore_20d"),
            rolling_zscore("etf_net_flow_1d", self.long_window, "etf_flow_zscore_60d"),
            (
                flow.rolling_sum(self.short_window)
                - flow.rolling_sum(self.short_window).shift(self.short_window)
            ).alias("etf_flow_acceleration"),
            (flow > 0).cast(pl.Int8).rolling_sum(10).alias("etf_flow_positive_days_10d"),
        )
        if market_cap is not None and "btc_market_cap" in market_cap.columns:
            frame = frame.join(
                market_cap.select("open_time", "btc_market_cap"), on="open_time", how="left"
            ).with_columns(
                (pl.col("etf_net_flow_1d") / pl.col("btc_market_cap")).alias(
                    "etf_flow_vs_btc_market_cap"
                )
            )
        return frame
