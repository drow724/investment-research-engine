"""Price return and trend features."""

from dataclasses import dataclass

import polars as pl

from investment.core.data.point_in_time import PointInTimeDataset


@dataclass(frozen=True, slots=True)
class ReturnFeature:
    days: int

    def __post_init__(self) -> None:
        if self.days <= 0:
            raise ValueError("days must be positive")

    @property
    def name(self) -> str:
        return f"return_{self.days}d"

    def compute(self, dataset: PointInTimeDataset) -> pl.DataFrame:
        return dataset.frame().select(
            "open_time", (pl.col("close") / pl.col("close").shift(self.days) - 1).alias(self.name)
        )


@dataclass(frozen=True, slots=True)
class PriceVsMovingAverageFeature:
    days: int

    def __post_init__(self) -> None:
        if self.days <= 1:
            raise ValueError("days must be greater than one")

    @property
    def name(self) -> str:
        return f"price_vs_ma_{self.days}"

    def compute(self, dataset: PointInTimeDataset) -> pl.DataFrame:
        average = pl.col("close").rolling_mean(self.days)
        return dataset.frame().select(
            "open_time", (pl.col("close") / average - 1).alias(self.name)
        )
