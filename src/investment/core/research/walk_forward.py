"""Leakage-safe calendar walk-forward splits."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import polars as pl


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    train_years: int = 4
    validation_years: int = 1
    test_years: int = 1
    mode: Literal["rolling", "expanding"] = "rolling"

    def __post_init__(self) -> None:
        if min(self.train_years, self.validation_years, self.test_years) <= 0:
            raise ValueError("all split durations must be positive")


@dataclass(frozen=True, slots=True)
class WalkForwardSplit:
    train: pl.DataFrame
    validation: pl.DataFrame
    test: pl.DataFrame


def _add_years(value: datetime, years: int) -> datetime:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + years)


class WalkForwardSplitter:
    def __init__(self, config: WalkForwardConfig | None = None, **kwargs: object) -> None:
        self.config = config or WalkForwardConfig(**kwargs)  # type: ignore[arg-type]

    def split(
        self, dataset: pl.DataFrame, time_column: str = "open_time"
    ) -> list[WalkForwardSplit]:
        if time_column not in dataset.columns or dataset.is_empty():
            return []
        ordered = dataset.sort(time_column)
        first = ordered.get_column(time_column).min()
        last = ordered.get_column(time_column).max()
        if not isinstance(first, datetime) or not isinstance(last, datetime):
            raise ValueError(f"{time_column} must be a datetime column")

        splits: list[WalkForwardSplit] = []
        step = 0
        while True:
            train_start = first if self.config.mode == "expanding" else _add_years(first, step)
            train_end = _add_years(first, self.config.train_years + step)
            validation_end = _add_years(train_end, self.config.validation_years)
            test_end = _add_years(validation_end, self.config.test_years)
            if validation_end > last:
                # At least one observation must exist in the test interval.
                break
            train = ordered.filter(
                (pl.col(time_column) >= train_start) & (pl.col(time_column) < train_end)
            )
            validation = ordered.filter(
                (pl.col(time_column) >= train_end) & (pl.col(time_column) < validation_end)
            )
            test = ordered.filter(
                (pl.col(time_column) >= validation_end) & (pl.col(time_column) < test_end)
            )
            if train.is_empty() or validation.is_empty() or test.is_empty():
                break
            splits.append(WalkForwardSplit(train, validation, test))
            step += self.config.test_years
        return splits
