"""Reusable Binance public REST ingestion and normalization."""

from datetime import UTC, datetime
from typing import Any

import httpx
import polars as pl

from investment.core.data.provider import RawMarketData
from investment.core.domain.observation import require_utc
from investment.market_data.crypto.schema import OHLCV_COLUMNS, OHLCV_SCHEMA


class BinanceCryptoPriceProvider:
    source = "binance"
    _interval_ms = 86_400_000

    def __init__(
        self,
        base_url: str = "https://api.binance.com",
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._client = client

    def fetch(self, symbol: str, start: datetime, end: datetime) -> RawMarketData:
        start_utc = require_utc(start, "start")
        end_utc = require_utc(end, "end")
        if start_utc >= end_utc:
            raise ValueError("start must precede end")
        start_ms = int(start_utc.timestamp() * 1000)
        end_ms = int(end_utc.timestamp() * 1000)
        records: list[list[Any]] = []
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout_seconds)
        try:
            cursor = start_ms
            while cursor < end_ms:
                response = client.get(
                    f"{self._base_url}/api/v3/klines",
                    params={
                        "symbol": symbol,
                        "interval": "1d",
                        "startTime": cursor,
                        "endTime": end_ms - 1,
                        "limit": 1000,
                    },
                )
                response.raise_for_status()
                page = response.json()
                if not isinstance(page, list):
                    raise ValueError("unexpected Binance response")
                if not page:
                    break
                records.extend(page)
                next_cursor = int(page[-1][0]) + self._interval_ms
                if next_cursor <= cursor:
                    raise RuntimeError("Binance pagination did not advance")
                cursor = next_cursor
                if len(page) < 1000:
                    break
        finally:
            if owns_client:
                client.close()
        return RawMarketData(
            symbol,
            start_utc,
            end_utc,
            self.source,
            [record for record in records if start_ms <= int(record[0]) < end_ms],
        )


class BinanceDailyNormalizer:
    source = "binance"

    def normalize(self, raw: RawMarketData, ingested_at: datetime | None = None) -> pl.DataFrame:
        if raw.source != self.source:
            raise ValueError(f"unsupported source: {raw.source}")
        ingestion_time = ingested_at or datetime.now(UTC)
        if ingestion_time.tzinfo is None:
            raise ValueError("ingested_at must be timezone-aware")
        rows = [
            {
                "symbol": raw.symbol,
                "open_time": datetime.fromtimestamp(int(record[0]) / 1000, tz=UTC),
                "available_at": datetime.fromtimestamp(int(record[6]) / 1000, tz=UTC),
                "ingested_at": ingestion_time.astimezone(UTC),
                "open": float(record[1]),
                "high": float(record[2]),
                "low": float(record[3]),
                "close": float(record[4]),
                "volume": float(record[5]),
                "source": raw.source,
            }
            for record in raw.records
        ]
        if not rows:
            return pl.DataFrame(schema=OHLCV_SCHEMA)
        return pl.DataFrame(rows).select(OHLCV_COLUMNS).cast(OHLCV_SCHEMA).sort("open_time")


# Transitional public name retained for existing API composition.
BinanceBitcoinPriceProvider = BinanceCryptoPriceProvider


def utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
