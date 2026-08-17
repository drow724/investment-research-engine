"""Upbit public REST adapter for KRW market discovery and daily candles."""

import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Lock
from typing import Any

import httpx

from investment.core.domain.observation import require_utc
from investment.crypto.domain.market import (
    MarketCandle,
    MarketDataBundle,
    TradingPair,
    TradingUniverse,
)
from investment.crypto.domain.timeframe import CandleTimeframe


@dataclass(frozen=True, slots=True)
class RawUpbitCandleBatch:
    pair: TradingPair
    start: datetime
    end: datetime
    ingested_at: datetime
    source: str
    records: Sequence[Mapping[str, Any]]
    timeframe: CandleTimeframe = CandleTimeframe.DAY_1


@dataclass(frozen=True, slots=True)
class UpbitMarketRecord:
    market: str
    korean_name: str
    english_name: str
    warning: bool


@dataclass(frozen=True, slots=True)
class UpbitTickerRecord:
    market: str
    accumulated_trade_price_24h: Decimal


class UpbitRateLimiter:
    """Serialize public requests and honor Upbit's remaining-request signal."""

    def __init__(
        self,
        requests_per_second: float = 8.0,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self._minimum_interval = 1.0 / requests_per_second
        self._sleeper = sleeper
        self._clock = clock
        self._next_request_at = 0.0
        self._lock = Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            delay = max(0.0, self._next_request_at - now)
            if delay:
                self._sleeper(delay)
                now = self._clock()
            self._next_request_at = max(now, self._next_request_at) + self._minimum_interval

    def defer(self, seconds: float) -> None:
        with self._lock:
            self._next_request_at = max(self._next_request_at, self._clock() + seconds)


_PROCESS_UPBIT_RATE_LIMITER = UpbitRateLimiter(requests_per_second=8.0)


class UpbitPublicClient:
    source = "upbit"

    def __init__(
        self,
        base_url: str = "https://api.upbit.com",
        client: httpx.Client | None = None,
        *,
        rate_limiter: UpbitRateLimiter | None = None,
        maximum_rate_limit_retries: int = 4,
        jitter: Callable[[], float] = lambda: random.uniform(0.05, 0.25),
    ) -> None:
        if maximum_rate_limit_retries < 0:
            raise ValueError("maximum_rate_limit_retries cannot be negative")
        self.base_url = base_url.rstrip("/")
        self._client = client
        self._limiter = rate_limiter or _PROCESS_UPBIT_RATE_LIMITER
        self._maximum_rate_limit_retries = maximum_rate_limit_retries
        self._jitter = jitter

    def rank_markets_by_quote_volume(
        self, markets: tuple[str, ...]
    ) -> tuple[UpbitTickerRecord, ...]:
        if not markets:
            return ()
        records: list[UpbitTickerRecord] = []
        for index in range(0, len(markets), 100):
            payload = self._get_json(
                "/v1/ticker", {"markets": ",".join(markets[index : index + 100])}
            )
            if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
                raise ValueError("unexpected Upbit ticker response")
            records.extend(
                UpbitTickerRecord(str(item["market"]), Decimal(str(item["acc_trade_price_24h"])))
                for item in payload
            )
        return tuple(
            sorted(
                records,
                key=lambda item: (-item.accumulated_trade_price_24h, item.market),
            )
        )

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
            record for record in records if start_utc <= _upbit_candle_time(record) < end_utc
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

    def fetch_minute_candles(
        self,
        pair: TradingPair,
        start: datetime,
        end: datetime,
        timeframe: CandleTimeframe,
    ) -> RawUpbitCandleBatch:
        start_utc = require_utc(start, "start")
        end_utc = require_utc(end, "end")
        if start_utc >= end_utc:
            raise ValueError("start must precede end")
        unit = timeframe.upbit_unit
        cursor = end_utc
        records: list[Mapping[str, Any]] = []
        while cursor > start_utc:
            payload = self._get_json(
                f"/v1/candles/minutes/{unit}",
                {
                    "market": to_upbit_market(pair),
                    "to": cursor.isoformat(),
                    "count": 200,
                },
            )
            if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
                raise ValueError("unexpected Upbit minute candle response")
            if not payload:
                break
            records.extend(payload)
            earliest = min(_upbit_candle_time(item) for item in payload)
            if len(payload) < 200 or earliest <= start_utc:
                break
            if earliest >= cursor:
                raise RuntimeError("Upbit minute candle pagination did not advance")
            cursor = earliest
        filtered = [
            record for record in records if start_utc <= _upbit_candle_time(record) < end_utc
        ]
        filtered.sort(key=_upbit_candle_time)
        return RawUpbitCandleBatch(
            pair,
            start_utc,
            end_utc,
            datetime.now(UTC),
            self.source,
            filtered,
            timeframe,
        )

    def _get_json(self, path: str, params: Mapping[str, str | int]) -> Any:
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=30)
        try:
            for attempt in range(self._maximum_rate_limit_retries + 1):
                self._limiter.wait()
                response = client.get(f"{self.base_url}{path}", params=params)
                self._observe_remaining_requests(response)
                if response.status_code == 429 and attempt < self._maximum_rate_limit_retries:
                    delay = _retry_delay(response, attempt) + self._jitter()
                    self._limiter.defer(delay)
                    continue
                response.raise_for_status()
                return response.json()
            raise RuntimeError("unreachable Upbit retry state")
        finally:
            if owns_client:
                client.close()

    def _observe_remaining_requests(self, response: httpx.Response) -> None:
        header = response.headers.get("Remaining-Req", "")
        match = re.search(r"(?:^|;)\s*sec=(\d+)", header, re.IGNORECASE)
        if match is not None and int(match.group(1)) == 0:
            self._limiter.defer(1.05)


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            pass
    return min(8.0, float(2**attempt))


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


class UpbitMinuteCandleNormalizer:
    def normalize(self, batch: RawUpbitCandleBatch) -> tuple[MarketCandle, ...]:
        if batch.timeframe is CandleTimeframe.DAY_1:
            raise ValueError("minute normalizer requires an intraday timeframe")
        duration = timedelta(minutes=batch.timeframe.minutes)
        candles = tuple(
            MarketCandle(
                pair=batch.pair,
                open_time=_upbit_candle_time(record),
                available_at=_upbit_candle_time(record) + duration,
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
            raise ValueError("normalized Upbit minute candles must be strictly ordered")
        return candles


class UpbitCryptoMarketDataProvider:
    def __init__(
        self,
        client: UpbitPublicClient,
        normalizer: UpbitDailyCandleNormalizer | None = None,
    ) -> None:
        self.client = client
        self.normalizer = normalizer or UpbitDailyCandleNormalizer()

    def fetch(self, universe: TradingUniverse, start: datetime, end: datetime) -> MarketDataBundle:
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
