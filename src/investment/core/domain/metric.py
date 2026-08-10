"""Vendor-neutral long-form external market metric."""

from dataclasses import dataclass
from datetime import datetime

from investment.core.domain.observation import require_utc


@dataclass(frozen=True, slots=True)
class MarketMetric:
    dataset: str
    metric: str
    event_time: datetime
    available_at: datetime
    ingested_at: datetime
    valid_from: datetime
    value: float
    source: str
    revision: int = 0
    entity: str | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        for field in ("event_time", "available_at", "ingested_at", "valid_from"):
            object.__setattr__(self, field, require_utc(getattr(self, field), field))
