"""Purged calendar walk-forward splits for overlapping forward labels."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

import polars as pl


class WalkForwardMode(StrEnum):
    ROLLING = "ROLLING"
    EXPANDING = "EXPANDING"


@dataclass(frozen=True, slots=True)
class PurgedWalkForwardConfig:
    train_days: int = 365
    validation_days: int = 90
    test_days: int = 90
    step_days: int = 90
    purge_days: int = 30
    mode: WalkForwardMode = WalkForwardMode.ROLLING

    def __post_init__(self) -> None:
        if min(
            self.train_days,
            self.validation_days,
            self.test_days,
            self.step_days,
            self.purge_days,
        ) <= 0:
            raise ValueError("walk-forward durations must be positive")


@dataclass(frozen=True, slots=True)
class PurgedWalkForwardSplit:
    fold: int
    train: pl.DataFrame
    validation: pl.DataFrame
    test: pl.DataFrame


class PurgedWalkForwardSplitter:
    def __init__(self, config: PurgedWalkForwardConfig) -> None:
        self.config = config

    def split(self, frame: pl.DataFrame) -> tuple[PurgedWalkForwardSplit, ...]:
        if frame.is_empty() or "as_of" not in frame.columns:
            return ()
        first_value = frame.get_column("as_of").min()
        last_value = frame.get_column("as_of").max()
        if not isinstance(first_value, datetime) or not isinstance(last_value, datetime):
            return ()
        first = first_value
        last = last_value
        splits = []
        fold = 0
        while True:
            offset = timedelta(days=fold * self.config.step_days)
            train_start = first if self.config.mode is WalkForwardMode.EXPANDING else first + offset
            train_end = first + timedelta(days=self.config.train_days) + offset
            validation_start = train_end + timedelta(days=self.config.purge_days)
            validation_end = validation_start + timedelta(days=self.config.validation_days)
            test_start = validation_end + timedelta(days=self.config.purge_days)
            test_end = test_start + timedelta(days=self.config.test_days)
            if test_end > last + timedelta(days=1):
                break
            train = frame.filter(
                (pl.col("as_of") >= train_start) & (pl.col("as_of") < train_end)
            )
            validation = frame.filter(
                (pl.col("as_of") >= validation_start)
                & (pl.col("as_of") < validation_end)
            )
            test = frame.filter(
                (pl.col("as_of") >= test_start) & (pl.col("as_of") < test_end)
            )
            if train.is_empty() or validation.is_empty() or test.is_empty():
                break
            splits.append(PurgedWalkForwardSplit(fold, train, validation, test))
            fold += 1
        return tuple(splits)
