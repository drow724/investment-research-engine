"""Multi-pair Upbit candle synchronization use case."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from investment.crypto.application.backtest_service import build_universe
from investment.crypto.domain.market import TradingUniverse
from investment.crypto.infrastructure.storage import (
    CryptoCandleParquetStorage,
    CryptoRawCandleStorage,
)
from investment.crypto.infrastructure.upbit import UpbitDailyCandleNormalizer, UpbitPublicClient


@dataclass(frozen=True, slots=True)
class CryptoPairSyncResult:
    pair: str
    rows: int
    raw_path: Path
    normalized_path: Path


class CryptoMarketDataService:
    def __init__(
        self,
        client: UpbitPublicClient,
        raw_storage: CryptoRawCandleStorage,
        normalized_storage: CryptoCandleParquetStorage,
    ) -> None:
        self._client = client
        self._raw_storage = raw_storage
        self._normalized_storage = normalized_storage
        self._normalizer = UpbitDailyCandleNormalizer()

    def sync(
        self, universe: TradingUniverse, start: datetime, end: datetime
    ) -> tuple[CryptoPairSyncResult, ...]:
        results = []
        for pair in universe.pairs:
            batch = self._client.fetch_daily_candles(pair, start, end)
            raw_path = self._raw_storage.save(batch)
            candles = self._normalizer.normalize(batch)
            normalized_path = self._normalized_storage.save(
                pair, candles, source=batch.source, ingested_at=batch.ingested_at
            )
            results.append(
                CryptoPairSyncResult(pair.symbol, len(candles), raw_path, normalized_path)
            )
        return tuple(results)

    def sync_pairs(
        self, pair_symbols: tuple[str, ...], start: datetime, end: datetime
    ) -> tuple[CryptoPairSyncResult, ...]:
        return self.sync(build_universe(pair_symbols), start, end)
