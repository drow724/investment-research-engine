"""Binance public REST adapter for daily BTC OHLCV."""

from datetime import UTC, datetime
from typing import Any

import httpx

from investment.core.data.provider import RawMarketData
from investment.core.domain.observation import require_utc


class BinanceBitcoinPriceProvider:
    """Fetch complete daily kline pages from Binance's exchange API."""

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
                last_open = int(page[-1][0])
                next_cursor = last_open + self._interval_ms
                if next_cursor <= cursor:
                    raise RuntimeError("Binance pagination did not advance")
                cursor = next_cursor
                if len(page) < 1000:
                    break
        finally:
            if owns_client:
                client.close()
        filtered = [record for record in records if start_ms <= int(record[0]) < end_ms]
        return RawMarketData(
            symbol=symbol,
            start=start_utc,
            end=end_utc,
            source=self.source,
            records=filtered,
        )


def utc_datetime(value: str) -> datetime:
    """Parse an ISO date or timestamp for simple programmatic use."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
