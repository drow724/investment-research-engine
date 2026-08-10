"""Configurable HTTP adapter for vendor-exported spot ETF flow records."""

from datetime import datetime
from typing import Any

import httpx

from investment.core.data.provider import RawMetricBatch
from investment.core.domain.observation import require_utc


class HttpBitcoinEtfFlowProvider:
    """Fetch records using a small vendor-neutral JSON boundary.

    The endpoint may return a list or ``{"records": [...]}``. Each record must contain
    event_time, fund/entity, value, and a publication timestamp understood by its normalizer.
    """

    def __init__(
        self,
        endpoint: str,
        source: str,
        *,
        client: httpx.Client | None = None,
        api_key: str | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.source = source
        self._client = client
        self._api_key = api_key

    def fetch(self, start: datetime, end: datetime) -> RawMetricBatch:
        start_utc = require_utc(start, "start")
        end_utc = require_utc(end, "end")
        if start_utc >= end_utc:
            raise ValueError("start must precede end")
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=30)
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            response = client.get(
                self.endpoint,
                params={"start": start_utc.isoformat(), "end": end_utc.isoformat()},
                headers=headers,
            )
            response.raise_for_status()
            payload: Any = response.json()
        finally:
            if owns_client:
                client.close()
        records = payload.get("records") if isinstance(payload, dict) else payload
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise ValueError("ETF endpoint must return a list of record objects")
        return RawMetricBatch("etf", start_utc, end_utc, self.source, records)
