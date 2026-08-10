"""Forward returns and path maximum drawdown labels."""

from dataclasses import dataclass

import numpy as np
import polars as pl

from investment.core.data.point_in_time import PointInTimeDataset


def _forward_max_drawdown(values: np.ndarray, horizon: int) -> np.ndarray:
    result = np.full(len(values), np.nan, dtype=np.float64)
    for index in range(0, len(values) - horizon):
        path = values[index : index + horizon + 1]
        peaks = np.maximum.accumulate(path)
        result[index] = float(np.min(path / peaks - 1.0))
    return result


@dataclass(frozen=True, slots=True)
class ForwardLabelGenerator:
    return_horizons: tuple[int, ...] = (7, 30, 90, 180)
    mdd_horizons: tuple[int, ...] = (30, 90)

    def __post_init__(self) -> None:
        if any(value <= 0 for value in (*self.return_horizons, *self.mdd_horizons)):
            raise ValueError("label horizons must be positive")

    def compute(self, dataset: PointInTimeDataset) -> pl.DataFrame:
        frame = dataset.frame().select("open_time", "close")
        close = frame.get_column("close").to_numpy()
        expressions = [
            (pl.col("close").shift(-horizon) / pl.col("close") - 1).alias(
                f"forward_return_{horizon}d"
            )
            for horizon in self.return_horizons
        ]
        result = frame.with_columns(expressions).drop("close")
        for horizon in self.mdd_horizons:
            values = _forward_max_drawdown(close, horizon)
            result = result.with_columns(
                pl.Series(f"forward_mdd_{horizon}d", values, nan_to_null=True)
            )
        return result
