"""Upbit public REST adapter for KRW market discovery and daily candles."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from investment.core.domain.observation import require_utc
from investment.crypto.domain.market import (
    MarketCandle,
    MarketDataBundle,
    TradingPair,
    TradingUniverse,
)


@dataclass(frozen=True, slots=True)
class RawUpbitCandleBatch:
    pair: TradingPair
    start: datetime
    end: datetime
    ingested_at: datetime
    source: str
    records: Sequence[Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class UpbitMarketRecord:
    market: str
    korean_name: str
    english_name: str
    warning: bool


class UpbitPublicClient:
    source = "upbit"

    def __init__(
        self,
        base_url: str = "https://api.upbit.com",
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client

    def list_markets(self) -> tuple[UpbitMarketRecord, ...]:
        payload = self._get_json("/v1/market/all", {"isDetails": "true"})
        if not isinstance(payload, list):
            raise ValueError("unexpected Upbit market response")
        records = []
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("Upbit market item must be an object")
            event = item.get("market_event")
            warning = bool(event.get("warning", False)) if isinstance(event, dict) else False
            records.append(
                UpbitMarketRecord(
                    market=str(item["market"]),
                    korean_name=str(item.get("korean_name", "")),
                    english_name=str(item.get("english_name", "")),
                    warning=warning,
                )
            )
        return tuple(records)

    def fetch_daily_candles(
        self, pair: TradingPair, start: datetime, end: datetime
    ) -> RawUpbitCandleBatch:
        start_utc = require_utc(start, "start")
        end_utc = require_utc(end, "end")
        if start_utc >= end_utc:
            raise ValueError("start must precede end")
        market = to_upbit_market(pair)
        cursor = end_utc
        records: list[Mapping[str, Any]] = []
        while cursor > start_utc:
            payload = self._get_json(
                "/v1/candles/days",
                {"market": market, "to": cursor.isoformat(), "count": 200},
            )
            if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
                raise ValueError("unexpected Upbit daily candle response")
            if not payload:
                break
            records.extend(payload)
            earliest = min(_upbit_candle_time(item) for item in payload)
            if len(payload) < 200 or earliest <= start_utc:
                break
            if earliest >= cursor:
                raise RuntimeError("Upbit candle pagination did not advance")
            cursor = earliest
        filtered = [
            record
            for record in records
            if start_utc <= _upbit_candle_time(record) < end_utc
        ]
        filtered.sort(key=_upbit_candle_time)
        return RawUpbitCandleBatch(
            pair,
            start_utc,
            end_utc,
            datetime.now(UTC),
            self.source,
            filtered,
        )

    def _get_json(self, path: str, params: Mapping[str, str | int]) -> Any:
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=30)
        try:
            response = client.get(f"{self.base_url}{path}", params=params)
            response.raise_for_status()
            return response.json()
        finally:
            if owns_client:
                client.close()


class UpbitDailyCandleNormalizer:
    def normalize(self, batch: RawUpbitCandleBatch) -> tuple[MarketCandle, ...]:
        candles = tuple(
            MarketCandle(
                pair=batch.pair,
                open_time=_upbit_candle_time(record),
                # A completed UTC daily candle is knowable at the next UTC day boundary.
                available_at=_upbit_candle_time(record) + timedelta(days=1),
                open=Decimal(str(record["opening_price"])),
                high=Decimal(str(record["high_price"])),
                low=Decimal(str(record["low_price"])),
                close=Decimal(str(record["trade_price"])),
                volume=Decimal(str(record["candle_acc_trade_volume"])),
            )
            for record in batch.records
        )
        if any(
            left.open_time >= right.open_time
            for left, right in zip(candles, candles[1:], strict=False)
        ):
            raise ValueError("normalized Upbit candles must be strictly ordered")
        return candles


class UpbitCryptoMarketDataProvider:
    def __init__(
        self,
        client: UpbitPublicClient,
        normalizer: UpbitDailyCandleNormalizer | None = None,
    ) -> None:
        self.client = client
        self.normalizer = normalizer or UpbitDailyCandleNormalizer()

    def fetch(
        self, universe: TradingUniverse, start: datetime, end: datetime
    ) -> MarketDataBundle:
        candles = {
            pair.symbol: self.normalizer.normalize(
                self.client.fetch_daily_candles(pair, start, end)
            )
            for pair in universe.pairs
        }
        return MarketDataBundle(universe, candles)


def to_upbit_market(pair: TradingPair) -> str:
    return f"{pair.quote.symbol}-{pair.base.symbol}"


def _upbit_candle_time(record: Mapping[str, Any]) -> datetime:
    value = record.get("candle_date_time_utc")
    if not isinstance(value, str):
        raise ValueError("Upbit candle is missing candle_date_time_utc")
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
