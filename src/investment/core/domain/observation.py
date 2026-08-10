"""Point-in-time market observation domain model."""

from dataclasses import dataclass
from datetime import UTC, datetime


def require_utc(value: datetime, field_name: str) -> datetime:
    """Return a UTC datetime, rejecting naive timestamps."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class MarketObservation:
    """A value with separate economic, publication, and ingestion times."""

    symbol: str
    metric: str
    event_time: datetime
    available_at: datetime
    ingested_at: datetime
    value: float
    source: str
    revision: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_time", require_utc(self.event_time, "event_time"))
        object.__setattr__(self, "available_at", require_utc(self.available_at, "available_at"))
        object.__setattr__(self, "ingested_at", require_utc(self.ingested_at, "ingested_at"))
        if self.available_at < self.event_time:
            raise ValueError("available_at cannot precede event_time")
