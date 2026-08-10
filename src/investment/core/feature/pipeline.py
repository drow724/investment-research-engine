"""Composition of independently testable features."""

import polars as pl

from investment.core.data.point_in_time import PointInTimeDataset
from investment.core.feature.base import Feature


class FeaturePipeline:
    def __init__(self, features: list[Feature]) -> None:
        names = [feature.name for feature in features]
        if len(names) != len(set(names)):
            raise ValueError("feature names must be unique")
        self._features = tuple(features)

    def compute(self, dataset: PointInTimeDataset) -> pl.DataFrame:
        base = dataset.frame().select("open_time")
        for feature in self._features:
            result = feature.compute(dataset)
            if result.columns != ["open_time", feature.name]:
                raise ValueError(
                    f"feature {feature.name} must return ['open_time', '{feature.name}']"
                )
            base = base.join(result, on="open_time", how="left", validate="1:1")
        return base.sort("open_time")
