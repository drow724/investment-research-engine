"""Reusable sync use case for ETF, on-chain, and derivatives metric batches."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from investment.bitcoin.data.normalization import LongFormMetricNormalizer
from investment.core.data.provider import MetricDataProvider
from investment.core.data.storage import MetricParquetStorage, RawMetricJsonStorage


@dataclass(frozen=True, slots=True)
class MetricDataSyncResult:
    dataset: str
    rows: int
    raw_path: Path
    normalized_path: Path


class MetricDataService:
    def __init__(
        self,
        provider: MetricDataProvider,
        normalizer: LongFormMetricNormalizer,
        raw_storage: RawMetricJsonStorage,
        normalized_storage: MetricParquetStorage,
    ) -> None:
        self._provider = provider
        self._normalizer = normalizer
        self._raw_storage = raw_storage
        self._normalized_storage = normalized_storage

    def sync(self, start: datetime, end: datetime) -> MetricDataSyncResult:
        batch = self._provider.fetch(start, end)
        raw_path = self._raw_storage.save(batch)
        frame = self._normalizer.normalize(batch)
        normalized_path = self._normalized_storage.save(batch.dataset, frame)
        return MetricDataSyncResult(batch.dataset, frame.height, raw_path, normalized_path)
