"""Point-in-time-safe tabular access."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Self

import polars as pl

from investment.core.domain.observation import require_utc


class PointInTimeDataset:
    """A market dataset exposing only information available by its as-of time."""

    REQUIRED_COLUMNS = {"available_at"}

    def __init__(self, frame: pl.DataFrame, as_of: datetime) -> None:
        missing = self.REQUIRED_COLUMNS.difference(frame.columns)
        if missing:
            raise ValueError(f"missing point-in-time columns: {sorted(missing)}")
        if "open_time" not in frame.columns and "event_time" not in frame.columns:
            raise ValueError("point-in-time dataset requires open_time or event_time")
        self._as_of = require_utc(as_of, "as_of")
        normalized = self._normalize_timestamps(frame)
        known = normalized.filter(pl.col("available_at") <= pl.lit(self._as_of))
        if "valid_from" in known.columns:
            known = known.filter(pl.col("valid_from") <= pl.lit(self._as_of))
        self._frame = self._select_known_revisions(known).sort(self.time_column_for(known))

    @staticmethod
    def _normalize_timestamps(frame: pl.DataFrame) -> pl.DataFrame:
        expressions: list[pl.Expr] = []
        for column in ("open_time", "event_time", "available_at", "ingested_at", "valid_from"):
            if column not in frame.columns:
                continue
            dtype = frame.schema[column]
            if dtype == pl.Utf8:
                expressions.append(
                    pl.col(column).str.to_datetime(time_zone="UTC", strict=True).alias(column)
                )
            elif isinstance(dtype, pl.Datetime):
                if dtype.time_zone is None:
                    expressions.append(pl.col(column).dt.replace_time_zone("UTC").alias(column))
                elif dtype.time_zone != "UTC":
                    expressions.append(pl.col(column).dt.convert_time_zone("UTC").alias(column))
        return frame.with_columns(expressions) if expressions else frame

    @staticmethod
    def time_column_for(frame: pl.DataFrame) -> str:
        return "open_time" if "open_time" in frame.columns else "event_time"

    @classmethod
    def _select_known_revisions(cls, frame: pl.DataFrame) -> pl.DataFrame:
        if not {"event_time", "metric", "revision"}.issubset(frame.columns):
            return frame
        entity_columns = [
            name
            for name in ("dataset", "source", "entity", "fund", "exchange", "instrument")
            if name in frame.columns
        ]
        key = [*entity_columns, "metric", "event_time"]
        valid_order = ["valid_from"] if "valid_from" in frame.columns else []
        ordering = ["available_at", *valid_order, "revision"]
        return frame.sort(ordering).unique(subset=key, keep="last")

    @classmethod
    def from_parquet(cls, path: str | Path, as_of: datetime) -> Self:
        path_value = Path(path)
        pattern = str(path_value / "*.parquet") if path_value.is_dir() else str(path_value)
        return cls(pl.read_parquet(pattern), as_of)

    @property
    def as_of(self) -> datetime:
        return self._as_of

    def frame(self) -> pl.DataFrame:
        """Return a defensive clone of the point-in-time-safe rows."""
        return self._frame.clone()

    def at(self, as_of: datetime) -> Self:
        """Create an earlier view; widening beyond the current cutoff is prohibited."""
        utc_as_of = require_utc(as_of, "as_of")
        if utc_as_of > self._as_of:
            raise ValueError("cannot widen a point-in-time dataset beyond its current as_of")
        return type(self)(self._frame, utc_as_of)

    @classmethod
    def latest(cls, frame: pl.DataFrame) -> Self:
        """Build a view containing all currently supplied rows."""
        if frame.is_empty():
            return cls(frame, datetime.now(UTC))
        normalized = cls._normalize_timestamps(frame)
        maximum = normalized.select(pl.col("available_at").max()).item()
        if not isinstance(maximum, datetime):
            raise ValueError("available_at must contain datetime values")
        return cls(normalized, maximum)
