from datetime import datetime
from typing import Protocol

from investment.crypto.domain.market import MarketDataBundle, MarketRegimeResult


class MarketRegimeModel(Protocol):
    def evaluate(self, market_data: MarketDataBundle, as_of: datetime) -> MarketRegimeResult: ...
