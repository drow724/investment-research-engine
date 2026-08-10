"""Annualized close-to-close realized volatility."""

from dataclasses import dataclass

import polars as pl

from investment.core.data.point_in_time import PointInTimeDataset


@dataclass(frozen=True, slots=True)
class RealizedVolatilityFeature:
    days: int
    annualization_days: int = 365

    def __post_init__(self) -> None:
        if self.days <= 1 or self.annualization_days <= 0:
            raise ValueError("window and annualization days must be positive")

    @property
    def name(self) -> str:
        return f"realized_vol_{self.days}d"

    def compute(self, dataset: PointInTimeDataset) -> pl.DataFrame:
        returns = pl.col("close").pct_change()
        value = returns.rolling_std(self.days) * (self.annualization_days**0.5)
        return dataset.frame().select("open_time", value.alias(self.name))
