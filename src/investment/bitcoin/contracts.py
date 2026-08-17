"""Published Bitcoin research results safe for other bounded contexts to consume."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from investment.core.domain.observation import require_utc


@dataclass(frozen=True, slots=True)
class BitcoinRegimeSnapshot:
    as_of: datetime
    regime: str
    accumulation_score: float
    confidence: float

    def __post_init__(self) -> None:
        require_utc(self.as_of, "as_of")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between zero and one")


class BitcoinResearchPort(Protocol):
    def latest_regime(self, as_of: datetime) -> BitcoinRegimeSnapshot: ...
