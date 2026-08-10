"""Cross-sectional momentum reference implementation."""

from dataclasses import dataclass
from decimal import Decimal

from investment.crypto.domain.market import Asset, MarketRegime
from investment.crypto.domain.signal import Signal, SignalDirection, StrategyResult
from investment.crypto.ports.strategy import StrategyContext


@dataclass(frozen=True, slots=True)
class CrossSectionalMomentumStrategy:
    lookback_days: int = 30
    maximum_assets: int = 3
    minimum_average_quote_volume: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.lookback_days <= 0 or self.maximum_assets <= 0:
            raise ValueError("lookback and maximum_assets must be positive")
        if self.minimum_average_quote_volume < 0:
            raise ValueError("minimum liquidity cannot be negative")

    @property
    def name(self) -> str:
        return "cross_sectional_momentum"

    @property
    def version(self) -> str:
        return "v1"

    def generate(self, context: StrategyContext) -> StrategyResult:
        known = context.market_data.known_at(context.as_of)
        if context.regime.regime is MarketRegime.RISK_OFF:
            return StrategyResult(self.name, self.version, context.as_of, context.regime.regime, ())
        candidates: list[tuple[Decimal, Asset]] = []
        for pair in known.universe.pairs:
            if (
                context.eligible_assets is not None
                and pair.base not in context.eligible_assets
            ):
                continue
            candles = known.candles[pair.symbol]
            if len(candles) < self.lookback_days + 1:
                continue
            window = candles[-self.lookback_days :]
            liquidity = sum(
                (candle.close * candle.volume for candle in window), Decimal("0")
            ) / Decimal(len(window))
            if liquidity < self.minimum_average_quote_volume:
                continue
            score = candles[-1].close / candles[-self.lookback_days - 1].close - Decimal("1")
            if score > 0:
                candidates.append((score, pair.base))
        ranked = sorted(candidates, key=lambda item: (-item[0], item[1].symbol))[
            : self.maximum_assets
        ]
        maximum = max((score for score, _ in ranked), default=Decimal("0"))
        signals = tuple(
            Signal(
                asset=asset,
                direction=SignalDirection.LONG,
                strength=min(score / maximum, Decimal("1")) if maximum else Decimal("0"),
                as_of=context.as_of,
                reason=f"positive_{self.lookback_days}d_cross_sectional_momentum",
            )
            for score, asset in ranked
        )
        return StrategyResult(
            self.name, self.version, context.as_of, context.regime.regime, signals
        )
