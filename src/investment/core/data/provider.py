"""Provider abstractions independent of exchange clients."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class RawMarketData:
    """Unmodified records plus metadata required for deterministic persistence."""

    symbol: str
    start: datetime
    end: datetime
    source: str
    records: list[list[Any]]


@dataclass(frozen=True, slots=True)
class RawMetricBatch:
    """Vendor payload retained before normalization into long-form metrics."""

    dataset: str
    start: datetime
    end: datetime
    source: str
    records: Sequence[Mapping[str, Any]]


class MarketDataProvider(Protocol):
    """Port implemented by external market-data adapters."""

    def fetch(self, symbol: str, start: datetime, end: datetime) -> RawMarketData:
        """Fetch raw observations in the half-open interval [start, end)."""
        ...


class MetricDataProvider(Protocol):
    def fetch(self, start: datetime, end: datetime) -> RawMetricBatch:
        """Fetch vendor records without applying research transformations."""
        ...
