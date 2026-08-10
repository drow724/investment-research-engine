"""Strategy output models; signals are not orders."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from investment.core.domain.observation import require_utc
from investment.crypto.domain.market import Asset, MarketRegime


class SignalDirection(StrEnum):
    LONG = "LONG"
    FLAT = "FLAT"


@dataclass(frozen=True, slots=True)
class Signal:
    asset: Asset
    direction: SignalDirection
    strength: Decimal
    as_of: datetime
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", require_utc(self.as_of, "as_of"))
        if not Decimal("0") <= self.strength <= Decimal("1"):
            raise ValueError("signal strength must be between zero and one")


@dataclass(frozen=True, slots=True)
class StrategyResult:
    strategy_name: str
    strategy_version: str
    as_of: datetime
    regime: MarketRegime
    signals: tuple[Signal, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", require_utc(self.as_of, "as_of"))
