"""Explicit policies for when external observations became knowable."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from investment.core.domain.observation import require_utc


class DataAvailabilityPolicy(Protocol):
    def resolve_available_at(self, raw_record: Mapping[str, Any]) -> datetime: ...


class PublishedTimestampPolicy:
    """Use a vendor publication timestamp without inventing a delay."""

    def __init__(self, field: str = "published_at") -> None:
        self.field = field

    def resolve_available_at(self, raw_record: Mapping[str, Any]) -> datetime:
        value = raw_record.get(self.field)
        if value is None:
            raise ValueError(f"missing publication timestamp: {self.field}")
        return parse_utc_datetime(value, self.field)


class FixedDelayAvailabilityPolicy:
    """Apply a documented deterministic delay to an event timestamp."""

    def __init__(self, delay: timedelta, event_field: str = "event_time") -> None:
        if delay < timedelta(0):
            raise ValueError("availability delay cannot be negative")
        self.delay = delay
        self.event_field = event_field

    def resolve_available_at(self, raw_record: Mapping[str, Any]) -> datetime:
        event_time = parse_utc_datetime(raw_record.get(self.event_field), self.event_field)
        return event_time + self.delay


def parse_utc_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return require_utc(value, field_name)
    if isinstance(value, (int, float)):
        divisor = 1000 if abs(value) > 10_000_000_000 else 1
        return datetime.fromtimestamp(value / divisor, tz=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return parsed.astimezone(UTC)
    raise ValueError(f"invalid datetime in {field_name}")
