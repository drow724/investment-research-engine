"""Deterministic raw and normalized storage for multi-asset crypto candles."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import polars as pl

from investment.crypto.domain.market import MarketCandle, TradingPair
from investment.crypto.domain.timeframe import CandleTimeframe
from investment.crypto.infrastructure.upbit import RawUpbitCandleBatch


def _slug(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


class CryptoRawCandleStorage:
    def __init__(self, root: str | Path = "data/raw/crypto/price") -> None:
        self.root = Path(root)

    def save(self, batch: RawUpbitCandleBatch) -> Path:
        directory = self.root / batch.source / batch.timeframe.value
        directory.mkdir(parents=True, exist_ok=True)
        canonical_records = json.dumps(
            batch.records, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str
        )
        content_hash = hashlib.sha256(canonical_records.encode()).hexdigest()[:16]
        path = directory / (
            f"{batch.pair.symbol}_{_slug(batch.start)}_{_slug(batch.end)}_{content_hash}.json"
        )
        payload = {
            "pair": batch.pair.symbol,
            "source": batch.source,
            "start": batch.start.isoformat(),
            "end": batch.end.isoformat(),
            "ingested_at": batch.ingested_at.isoformat(),
            "timeframe": batch.timeframe.value,
            "records": batch.records,
        }
        if path.exists():
            return path
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
            encoding="utf-8",
        )
        temporary.replace(path)
        return path


class CryptoCandleParquetStorage:
    def __init__(self, root: str | Path = "data/normalized/crypto/price") -> None:
        self.root = Path(root)

    def save(
        self,
        pair: TradingPair,
        candles: tuple[MarketCandle, ...],
        *,
        source: str,
        ingested_at: datetime,
        timeframe: CandleTimeframe = CandleTimeframe.DAY_1,
    ) -> Path:
        directory = self.root if timeframe is CandleTimeframe.DAY_1 else self.root / timeframe.value
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{pair.symbol}.parquet"
        frame = _candles_to_frame(candles, source, ingested_at)
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

    def exists(self, pair: TradingPair, timeframe: CandleTimeframe = CandleTimeframe.DAY_1) -> bool:
        directory = self.root if timeframe is CandleTimeframe.DAY_1 else self.root / timeframe.value
        return (directory / f"{pair.symbol}.parquet").exists()

    def row_count(
        self, pair: TradingPair, timeframe: CandleTimeframe = CandleTimeframe.DAY_1
    ) -> int:
        directory = self.root if timeframe is CandleTimeframe.DAY_1 else self.root / timeframe.value
        path = directory / f"{pair.symbol}.parquet"
        if not path.exists():
            return 0
        return int(pl.scan_parquet(path).select(pl.len()).collect().item())


def _candles_to_frame(
    candles: tuple[MarketCandle, ...], source: str, ingested_at: datetime
) -> pl.DataFrame:
    rows = [
        {
            "symbol": candle.pair.symbol,
            "open_time": candle.open_time,
            "available_at": candle.available_at,
            "ingested_at": ingested_at,
            "open": _float(candle.open),
            "high": _float(candle.high),
            "low": _float(candle.low),
            "close": _float(candle.close),
            "volume": _float(candle.volume),
            "source": source,
        }
        for candle in candles
    ]
    return (
        pl.DataFrame(rows)
        if rows
        else pl.DataFrame(
            schema={
                "symbol": pl.String,
                "open_time": pl.Datetime("us", "UTC"),
                "available_at": pl.Datetime("us", "UTC"),
                "ingested_at": pl.Datetime("us", "UTC"),
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Float64,
                "source": pl.String,
            }
        )
    )


def _float(value: Decimal) -> float:
    return float(value)
