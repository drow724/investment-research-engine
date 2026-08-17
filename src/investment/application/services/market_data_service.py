"""Market-data synchronization use case."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from investment.core.data.provider import MarketDataProvider
from investment.core.data.storage import NormalizedParquetStorage, RawJsonStorage
from investment.market_data.crypto.binance import BinanceDailyNormalizer


@dataclass(frozen=True, slots=True)
class MarketDataSyncResult:
    symbol: str
    rows: int
    raw_path: Path
    normalized_path: Path


class MarketDataService:
    def __init__(
        self,
        provider: MarketDataProvider,
        normalizer: BinanceDailyNormalizer,
        raw_storage: RawJsonStorage,
        normalized_storage: NormalizedParquetStorage,
    ) -> None:
        self._provider = provider
        self._normalizer = normalizer
        self._raw_storage = raw_storage
        self._normalized_storage = normalized_storage

    def sync(self, symbol: str, start: datetime, end: datetime) -> MarketDataSyncResult:
        raw = self._provider.fetch(symbol, start, end)
        raw_path = self._raw_storage.save(raw)
        normalized = self._normalizer.normalize(raw)
        normalized_path = self._normalized_storage.save(symbol, normalized)
        return MarketDataSyncResult(symbol, normalized.height, raw_path, normalized_path)
