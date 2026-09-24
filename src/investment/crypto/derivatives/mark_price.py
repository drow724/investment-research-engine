"""Timestamped Binance mark/index/funding observations."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from investment.core.domain.observation import require_utc

MAXIMUM_SOURCE_CLOCK_SKEW_SECONDS = 2


@dataclass(frozen=True, slots=True)
class MarkPriceObservation:
    symbol: str
    event_at: datetime
    received_at: datetime
    mark_price: Decimal
    index_price: Decimal
    funding_rate: Decimal
    next_funding_at: datetime

    def __post_init__(self) -> None:
        for name in ("event_at", "received_at", "next_funding_at"):
            object.__setattr__(self, name, require_utc(getattr(self, name), name))
        for value in (self.mark_price, self.index_price, self.funding_rate):
            if not value.is_finite():
                raise ValueError("nonfinite mark price observation")
        if self.mark_price <= 0 or self.index_price <= 0:
            raise ValueError("mark/index price must be positive")
        if self.event_at > self.received_at:
            raise ValueError("future mark price event")


def parse_mark_price(message: str | bytes, received_at: datetime) -> MarkPriceObservation | None:
    try:
        payload = json.loads(message)
    except (TypeError, json.JSONDecodeError):
        # Transport control/non-JSON frames are not Binance application messages.
        return None
    if not isinstance(payload, dict):
        raise ValueError("mark price payload must be an object")
    payload = payload.get("data", payload)
    if not isinstance(payload, dict):
        raise ValueError("mark price data must be an object")
    if payload.get("e") != "markPriceUpdate" or payload.get("s") != "BTCUSDT":
        return None
    if not all(field in payload for field in ("E", "p", "i", "r", "T")):
        return None
    event_at = datetime.fromtimestamp(int(payload["E"]) / 1000, UTC)
    local_received_at = require_utc(received_at, "received_at")
    if event_at > local_received_at:
        skew_seconds = (event_at - local_received_at).total_seconds()
        if skew_seconds > MAXIMUM_SOURCE_CLOCK_SKEW_SECONDS:
            raise ValueError("mark price event is too far ahead of local clock")
        # Source time ahead of the host clock is availability time. This is
        # conservative for point-in-time reads and avoids discarding valid ticks.
        local_received_at = event_at
    return MarkPriceObservation(
        symbol=payload["s"],
        event_at=event_at,
        received_at=local_received_at,
        mark_price=Decimal(str(payload["p"])),
        index_price=Decimal(str(payload["i"])),
        funding_rate=Decimal(str(payload["r"])),
        next_funding_at=datetime.fromtimestamp(int(payload["T"]) / 1000, UTC),
    )
