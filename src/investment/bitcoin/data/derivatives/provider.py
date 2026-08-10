"""Binance USD-M futures funding and open-interest adapter."""

from datetime import datetime
from typing import Any

import httpx

from investment.core.data.provider import RawMetricBatch
from investment.core.domain.observation import require_utc


class BinanceDerivativesProvider:
    source = "binance_futures"

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        base_url: str = "https://fapi.binance.com",
        client: httpx.Client | None = None,
    ) -> None:
        self.symbol = symbol
        self.base_url = base_url.rstrip("/")
        self._client = client

    def fetch(self, start: datetime, end: datetime) -> RawMetricBatch:
        start_utc = require_utc(start, "start")
        end_utc = require_utc(end, "end")
        start_ms = int(start_utc.timestamp() * 1000)
        end_ms = int(end_utc.timestamp() * 1000)
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=30)
        try:
            funding = self._fetch_paginated(
                client,
                "/fapi/v1/fundingRate",
                {"symbol": self.symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
                timestamp_field="fundingTime",
                limit=1000,
            )
            interest = self._fetch_paginated(
                client,
                "/futures/data/openInterestHist",
                {
                    "symbol": self.symbol,
                    "period": "1d",
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": 500,
                },
                timestamp_field="timestamp",
                limit=500,
            )
        finally:
            if owns_client:
                client.close()
        records = [
            {
                "entity": self.symbol,
                "metric": "funding_rate",
                "event_time": item["fundingTime"],
                "published_at": item["fundingTime"],
                "value": item["fundingRate"],
                "unit": "rate",
            }
            for item in funding
        ]
        records.extend(
            {
                "entity": self.symbol,
                "metric": "open_interest",
                "event_time": item["timestamp"],
                "published_at": item["timestamp"],
                "value": item["sumOpenInterestValue"],
                "unit": "USD",
            }
            for item in interest
        )
        return RawMetricBatch("derivatives", start_utc, end_utc, self.source, records)

    def _request(
        self, client: httpx.Client, path: str, params: dict[str, str | int]
    ) -> list[dict[str, Any]]:
        response = client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"unexpected Binance derivatives response for {path}")
        return payload

    def _fetch_paginated(
        self,
        client: httpx.Client,
        path: str,
        params: dict[str, str | int],
        *,
        timestamp_field: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        cursor = int(params["startTime"])
        end = int(params["endTime"])
        while cursor < end:
            page_params = {**params, "startTime": cursor}
            page = self._request(client, path, page_params)
            if not page:
                break
            records.extend(page)
            if len(page) < limit:
                break
            next_cursor = int(page[-1][timestamp_field]) + 1
            if next_cursor <= cursor:
                raise RuntimeError(f"pagination did not advance for {path}")
            cursor = next_cursor
        return records
