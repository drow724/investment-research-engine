"""Feature contract."""

from typing import Protocol

import polars as pl

from investment.core.data.point_in_time import PointInTimeDataset


class Feature(Protocol):
    @property
    def name(self) -> str: ...

    def compute(self, dataset: PointInTimeDataset) -> pl.DataFrame: ...
