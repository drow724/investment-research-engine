"""Pure conversion of model scores into long/flat strategy signals."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from investment.crypto.domain.market import Asset, MarketRegime
from investment.crypto.domain.signal import Signal, SignalDirection, StrategyResult


@dataclass(frozen=True, slots=True)
class RankedReturn:
    asset: str
    expected_return: float


@dataclass(frozen=True, slots=True)
class MLRankingStrategy:
    maximum_assets: int = 3
    minimum_expected_return: float = 0.0

    def __post_init__(self) -> None:
        if self.maximum_assets <= 0:
            raise ValueError("maximum_assets must be positive")

    def generate(
        self,
        predictions: tuple[RankedReturn, ...],
        *,
        model_id: str,
        as_of: datetime,
        regime: MarketRegime,
    ) -> StrategyResult:
        positive = [
            item
            for item in predictions
            if item.expected_return > self.minimum_expected_return
        ][: self.maximum_assets]
        maximum = max((item.expected_return for item in positive), default=0.0)
        signals = tuple(
            Signal(
                Asset(item.asset),
                SignalDirection.LONG,
                Decimal(str(item.expected_return / maximum)) if maximum > 0 else Decimal("0"),
                as_of,
                f"model={model_id}; expected_return={item.expected_return:.8f}",
            )
            for item in positive
        )
        return StrategyResult("ml_return_ranking", "1.0.0", as_of, regime, signals)
