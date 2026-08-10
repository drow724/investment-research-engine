from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from investment.crypto.domain.market import Asset, MarketDataBundle, MarketRegimeResult
from investment.crypto.domain.signal import StrategyResult


@dataclass(frozen=True, slots=True)
class StrategyContext:
    as_of: datetime
    market_data: MarketDataBundle
    regime: MarketRegimeResult
    eligible_assets: tuple[Asset, ...] | None = None


class Strategy(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def generate(self, context: StrategyContext) -> StrategyResult: ...
