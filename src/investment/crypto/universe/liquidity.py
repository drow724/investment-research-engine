"""Membership, warning, history, and trailing-liquidity eligibility rules."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from investment.crypto.domain.market import MarketDataBundle
from investment.crypto.domain.universe import (
    UniverseEligibilityResult,
    UniverseHistory,
)


@dataclass(frozen=True, slots=True)
class PointInTimeLiquidityUniverse:
    history: UniverseHistory
    lookback_days: int = 30
    minimum_average_quote_volume: Decimal = Decimal("1000000000")
    maximum_assets: int = 30
    require_btc: bool = True

    def __post_init__(self) -> None:
        if self.lookback_days <= 0 or self.maximum_assets <= 0:
            raise ValueError("universe lookback and maximum assets must be positive")
        if self.minimum_average_quote_volume < 0:
            raise ValueError("minimum liquidity cannot be negative")

    def evaluate(
        self, market_data: MarketDataBundle, as_of: datetime
    ) -> UniverseEligibilityResult:
        snapshot = self.history.known_at(as_of)
        if snapshot is None:
            return UniverseEligibilityResult(
                as_of,
                None,
                (),
                {},
                {pair.symbol: "NO_MEMBERSHIP_SNAPSHOT" for pair in market_data.universe.pairs},
            )
        membership = {member.pair.symbol: member for member in snapshot.members}
        liquidity: dict[str, Decimal] = {}
        exclusions: dict[str, str] = {}
        eligible = []
        known = market_data.known_at(as_of)
        for pair in market_data.universe.pairs:
            member = membership.get(pair.symbol)
            if member is None:
                exclusions[pair.symbol] = "NOT_IN_LATEST_KNOWN_SNAPSHOT"
                continue
            if member.warning:
                exclusions[pair.symbol] = "MARKET_WARNING"
                continue
            candles = known.candles[pair.symbol]
            if len(candles) < self.lookback_days:
                exclusions[pair.symbol] = "INSUFFICIENT_HISTORY"
                continue
            window = candles[-self.lookback_days :]
            average = sum(
                (candle.close * candle.volume for candle in window), Decimal("0")
            ) / Decimal(len(window))
            liquidity[pair.symbol] = average
            if average < self.minimum_average_quote_volume:
                exclusions[pair.symbol] = "INSUFFICIENT_LIQUIDITY"
                continue
            eligible.append(pair)
        ranked = sorted(eligible, key=lambda pair: (-liquidity[pair.symbol], pair.symbol))
        selected = ranked[: self.maximum_assets]
        if self.require_btc:
            btc = next((pair for pair in ranked if pair.base.symbol == "BTC"), None)
            if btc is not None and btc not in selected:
                selected = ([*selected[:-1], btc] if selected else [btc])
        selected_symbols = {pair.symbol for pair in selected}
        for pair in eligible:
            if pair.symbol not in selected_symbols:
                exclusions[pair.symbol] = "BELOW_LIQUIDITY_RANK_CUTOFF"
        return UniverseEligibilityResult(
            as_of,
            snapshot.observed_at,
            tuple(selected),
            liquidity,
            exclusions,
        )
