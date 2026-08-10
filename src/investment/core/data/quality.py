"""Fail-fast validation for normalized market metrics."""

from dataclasses import dataclass
from datetime import datetime

import polars as pl


class DataQualityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MetricQualitySpec:
    required_columns: tuple[str, ...]
    unique_by: tuple[str, ...]
    non_negative_metrics: frozenset[str] = frozenset()


class DataQualityValidator:
    def validate(self, frame: pl.DataFrame, spec: MetricQualitySpec) -> None:
        missing = set(spec.required_columns).difference(frame.columns)
        if missing:
            raise DataQualityError(f"schema mismatch; missing columns: {sorted(missing)}")
        if frame.select(pl.col(spec.required_columns).is_null().any()).row(0).count(True):
            raise DataQualityError("unexpected null in required columns")
        for column in ("event_time", "available_at", "ingested_at", "valid_from"):
            if column not in frame.columns:
                continue
            dtype = frame.schema[column]
            if not isinstance(dtype, pl.Datetime) or dtype.time_zone is None:
                raise DataQualityError(f"{column} must be timezone-aware")
        series_key = [
            column for column in ("source", "entity", "metric") if column in frame.columns
        ]
        partitions = frame.partition_by(series_key, maintain_order=True) if series_key else [frame]
        if any(
            partition.height > 1 and not partition.get_column("event_time").is_sorted()
            for partition in partitions
        ):
            raise DataQualityError("event_time must be sorted within each metric series")
        if frame.is_duplicated().any():
            raise DataQualityError("exact duplicate records detected")
        if spec.unique_by and frame.select(pl.struct(spec.unique_by).is_duplicated().any()).item():
            raise DataQualityError(f"duplicate key detected: {spec.unique_by}")
        numeric = [name for name, dtype in frame.schema.items() if dtype.is_numeric()]
        for column in numeric:
            if frame.select(pl.col(column).is_infinite().any()).item():
                raise DataQualityError(f"infinite value in {column}")
        if spec.non_negative_metrics and "metric" in frame.columns:
            invalid = frame.filter(
                pl.col("metric").is_in(list(spec.non_negative_metrics)) & (pl.col("value") < 0)
            )
            if not invalid.is_empty():
                raise DataQualityError("negative value for a non-negative metric")


def ensure_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise DataQualityError(f"{field} must contain datetimes")
    return value
