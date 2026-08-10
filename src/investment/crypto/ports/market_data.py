from datetime import datetime
from typing import Protocol

from investment.crypto.domain.market import MarketDataBundle, TradingUniverse


class CryptoMarketDataProvider(Protocol):
    def fetch(
        self, universe: TradingUniverse, start: datetime, end: datetime
    ) -> MarketDataBundle: ...
