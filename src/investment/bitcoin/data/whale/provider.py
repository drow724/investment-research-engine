from datetime import datetime
from typing import Protocol

from investment.core.data.provider import MetricDataProvider, RawMetricBatch


class LargeAddressMetricProvider(MetricDataProvider, Protocol):
    """Large addresses may be exchanges, custodians, funds, or individuals."""

    def fetch(self, start: datetime, end: datetime) -> RawMetricBatch: ...
