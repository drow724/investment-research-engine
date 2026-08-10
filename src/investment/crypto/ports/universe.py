from datetime import datetime
from typing import Protocol

from investment.crypto.domain.market import MarketDataBundle
from investment.crypto.domain.universe import UniverseEligibilityResult


class UniverseEligibilityModel(Protocol):
    def evaluate(
        self, market_data: MarketDataBundle, as_of: datetime
    ) -> UniverseEligibilityResult: ...
