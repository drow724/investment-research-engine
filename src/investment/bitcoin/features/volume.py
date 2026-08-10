"""Volume change, level, and standardized-volume features."""

from dataclasses import dataclass

import polars as pl

from investment.core.data.point_in_time import PointInTimeDataset


@dataclass(frozen=True, slots=True)
class VolumeChangeFeature:
    days: int = 1

    @property
    def name(self) -> str:
        return f"volume_change_{self.days}d"

    def compute(self, dataset: PointInTimeDataset) -> pl.DataFrame:
        value = pl.col("volume") / pl.col("volume").shift(self.days) - 1
        return dataset.frame().select("open_time", value.alias(self.name))


@dataclass(frozen=True, slots=True)
class VolumeMovingAverageFeature:
    days: int = 20

    @property
    def name(self) -> str:
        return f"volume_ma_{self.days}"

    def compute(self, dataset: PointInTimeDataset) -> pl.DataFrame:
        return dataset.frame().select(
            "open_time", pl.col("volume").rolling_mean(self.days).alias(self.name)
        )


@dataclass(frozen=True, slots=True)
class VolumeZScoreFeature:
    days: int = 20

    @property
    def name(self) -> str:
        return f"volume_zscore_{self.days}d"

    def compute(self, dataset: PointInTimeDataset) -> pl.DataFrame:
        volume = pl.col("volume")
        mean = volume.rolling_mean(self.days)
        std = volume.rolling_std(self.days)
        return dataset.frame().select("open_time", ((volume - mean) / std).alias(self.name))
