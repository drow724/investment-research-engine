"""Point-in-time trading-universe observations and eligibility results."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from investment.core.domain.observation import require_utc
from investment.crypto.domain.market import TradingPair


@dataclass(frozen=True, slots=True)
class UniverseMember:
    pair: TradingPair
    warning: bool
    source: str
    observed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", require_utc(self.observed_at, "observed_at"))


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    observed_at: datetime
    source: str
    members: tuple[UniverseMember, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", require_utc(self.observed_at, "observed_at"))
        symbols = [member.pair.symbol for member in self.members]
        if len(symbols) != len(set(symbols)):
            raise ValueError("universe snapshot contains duplicate pairs")
        if any(member.observed_at != self.observed_at for member in self.members):
            raise ValueError("member observation time must match its snapshot")


@dataclass(frozen=True, slots=True)
class UniverseHistory:
    snapshots: tuple[UniverseSnapshot, ...]

    def __post_init__(self) -> None:
        if any(
            left.observed_at >= right.observed_at
            for left, right in zip(self.snapshots, self.snapshots[1:], strict=False)
        ):
            raise ValueError("universe snapshots must be strictly ordered")

    def known_at(self, as_of: datetime) -> UniverseSnapshot | None:
        cutoff = require_utc(as_of, "as_of")
        known = [snapshot for snapshot in self.snapshots if snapshot.observed_at <= cutoff]
        return known[-1] if known else None


@dataclass(frozen=True, slots=True)
class UniverseEligibilityResult:
    as_of: datetime
    snapshot_observed_at: datetime | None
    eligible_pairs: tuple[TradingPair, ...]
    average_quote_volume: Mapping[str, Decimal]
    exclusions: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", require_utc(self.as_of, "as_of"))
