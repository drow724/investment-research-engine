from datetime import datetime
from typing import Protocol

from investment.core.data.provider import MetricDataProvider, RawMetricBatch


class BitcoinOnChainProvider(MetricDataProvider, Protocol):
    def fetch(self, start: datetime, end: datetime) -> RawMetricBatch: ...
