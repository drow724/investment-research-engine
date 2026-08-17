"""Helpers shared by concrete metric-family normalizers."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import polars as pl

from investment.bitcoin.data.metric_schema import METRIC_COLUMNS, METRIC_SCHEMA
from investment.core.data.availability import DataAvailabilityPolicy, parse_utc_datetime
from investment.core.data.provider import RawMetricBatch
from investment.core.data.quality import DataQualityValidator, MetricQualitySpec


class LongFormMetricNormalizer:
    """Normalize a vendor-neutral record mapping while preserving revisions."""

    def __init__(
        self,
        dataset: str,
        availability_policy: DataAvailabilityPolicy,
        *,
        default_metric: str | None = None,
        default_unit: str = "native",
        non_negative_metrics: frozenset[str] = frozenset(),
    ) -> None:
        self.dataset = dataset
        self.availability_policy = availability_policy
        self.default_metric = default_metric
        self.default_unit = default_unit
        self.non_negative_metrics = non_negative_metrics

    def normalize(self, batch: RawMetricBatch, ingested_at: datetime | None = None) -> pl.DataFrame:
        if batch.dataset != self.dataset:
            raise ValueError(f"expected {self.dataset} batch, got {batch.dataset}")
        ingestion = (ingested_at or datetime.now(UTC)).astimezone(UTC)
        rows = [self._normalize_record(record, batch.source, ingestion) for record in batch.records]
        if not rows:
            return pl.DataFrame(schema=METRIC_SCHEMA)
        frame = pl.DataFrame(rows).select(METRIC_COLUMNS).cast(METRIC_SCHEMA)
        DataQualityValidator().validate(
            frame,
            MetricQualitySpec(
                required_columns=tuple(METRIC_COLUMNS),
                unique_by=("source", "entity", "metric", "event_time", "revision"),
                non_negative_metrics=self.non_negative_metrics,
            ),
        )
        return frame.sort("event_time")

    def _normalize_record(
        self, record: Mapping[str, Any], source: str, ingested_at: datetime
    ) -> dict[str, object]:
        metric_value = record.get("metric", self.default_metric)
        if not isinstance(metric_value, str) or not metric_value:
            raise ValueError("metric is required")
        event_time = parse_utc_datetime(record.get("event_time"), "event_time")
        available_at = self.availability_policy.resolve_available_at(record)
        valid_raw = record.get("valid_from")
        valid_from = (
            parse_utc_datetime(valid_raw, "valid_from") if valid_raw is not None else available_at
        )
        return {
            "dataset": self.dataset,
            "entity": str(record.get("entity") or record.get("fund") or "aggregate"),
            "metric": metric_value,
            "event_time": event_time,
            "available_at": available_at,
            "ingested_at": ingested_at,
            "valid_from": valid_from,
            "value": float(record["value"]),
            "unit": str(record.get("unit") or record.get("currency") or self.default_unit),
            "source": source,
            "revision": int(record.get("revision", 0)),
        }
