"""Normalize Binance kline payloads without performing I/O."""

from datetime import UTC, datetime

import polars as pl

from investment.bitcoin.data.price.schema import OHLCV_COLUMNS, OHLCV_SCHEMA
from investment.core.data.provider import RawMarketData


class BinanceDailyNormalizer:
    source = "binance"

    def normalize(
        self, raw: RawMarketData, ingested_at: datetime | None = None
    ) -> pl.DataFrame:
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
