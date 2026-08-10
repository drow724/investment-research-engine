"""Deterministic raw JSON and normalized Parquet research storage."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from investment.core.data.provider import RawMarketData, RawMetricBatch


def _timestamp_slug(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


class RawJsonStorage:
    """Persist one canonical provider response per source/symbol/time range."""

    def __init__(self, root: str | Path = "data/raw") -> None:
        self.root = Path(root)

    def save(self, data: RawMarketData) -> Path:
        directory = self.root / "bitcoin" / "price" / data.source
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (
            f"{data.symbol}_{_timestamp_slug(data.start)}_{_timestamp_slug(data.end)}.json"
        )
        payload: dict[str, Any] = {
            "symbol": data.symbol,
            "source": data.source,
            "start": data.start.isoformat(),
            "end": data.end.isoformat(),
            "records": data.records,
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        temporary.replace(path)
        return path


class NormalizedParquetStorage:
    """Upsert normalized rows by source, symbol, and candle open time."""

    def __init__(self, root: str | Path = "data/normalized") -> None:
        self.root = Path(root)

    def save(self, symbol: str, frame: pl.DataFrame) -> Path:
        directory = self.root / "bitcoin" / "price"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{symbol}.parquet"
        combined = (
            pl.concat([pl.read_parquet(path), frame], how="diagonal_relaxed")
            if path.exists()
            else frame
        )
        combined = combined.unique(subset=["source", "symbol", "open_time"], keep="last").sort(
            "open_time"
        )
        temporary = path.with_suffix(".parquet.tmp")
        combined.write_parquet(temporary)
        temporary.replace(path)
        return path

    def read(self, symbol: str) -> pl.DataFrame:
        return pl.read_parquet(self.root / "bitcoin" / "price" / f"{symbol}.parquet")


class RawMetricJsonStorage:
    """Persist deterministic raw payloads for external metric families."""

    def __init__(self, root: str | Path = "data/raw") -> None:
        self.root = Path(root)

    def save(self, batch: RawMetricBatch) -> Path:
        directory = self.root / "bitcoin" / batch.dataset / batch.source
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (
            f"{_timestamp_slug(batch.start)}_{_timestamp_slug(batch.end)}.json"
        )
        payload = {
            "dataset": batch.dataset,
            "source": batch.source,
            "start": batch.start.isoformat(),
            "end": batch.end.isoformat(),
            "records": batch.records,
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
            encoding="utf-8",
        )
        temporary.replace(path)
        return path


class MetricParquetStorage:
    """Revision-preserving long-form metric storage."""

    def __init__(self, root: str | Path = "data/normalized") -> None:
        self.root = Path(root)

    def save(self, dataset: str, frame: pl.DataFrame) -> Path:
        directory = self.root / "bitcoin" / dataset
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "metrics.parquet"
        combined = (
            pl.concat([pl.read_parquet(path), frame], how="diagonal_relaxed")
            if path.exists()
            else frame
        )
        keys = [
            name
            for name in (
                "dataset",
                "source",
                "entity",
                "metric",
                "event_time",
                "revision",
                "valid_from",
            )
            if name in combined.columns
        ]
        combined = combined.unique(subset=keys, keep="last").sort("event_time")
        temporary = path.with_suffix(".parquet.tmp")
        combined.write_parquet(temporary)
        temporary.replace(path)
        return path

    def read(self, dataset: str) -> pl.DataFrame:
        return pl.read_parquet(self.root / "bitcoin" / dataset / "metrics.parquet")
