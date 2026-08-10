"""Assets, pairs, universe, candles, and deterministic market-regime values."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from investment.core.domain.observation import require_utc


class AssetKind(StrEnum):
    CRYPTO = "CRYPTO"
    CASH = "CASH"


@dataclass(frozen=True, slots=True, order=True)
class Asset:
    symbol: str
    kind: AssetKind = AssetKind.CRYPTO

    def __post_init__(self) -> None:
        normalized = self.symbol.strip().upper()
        if not normalized or not normalized.isalnum():
            raise ValueError("asset symbol must be non-empty and alphanumeric")
        object.__setattr__(self, "symbol", normalized)


@dataclass(frozen=True, slots=True)
class TradingPair:
    base: Asset
    quote: Asset

    def __post_init__(self) -> None:
        if self.base == self.quote:
            raise ValueError("base and quote assets must differ")
        if self.quote.kind is not AssetKind.CASH:
            raise ValueError("spot trading-pair quote must be a cash asset")

    @property
    def symbol(self) -> str:
        return f"{self.base.symbol}{self.quote.symbol}"


@dataclass(frozen=True, slots=True)
class TradingUniverse:
    pairs: tuple[TradingPair, ...]

    def __post_init__(self) -> None:
        if not self.pairs:
            raise ValueError("trading universe cannot be empty")
        symbols = [pair.symbol for pair in self.pairs]
        if len(symbols) != len(set(symbols)):
            raise ValueError("trading universe pairs must be unique")
        quotes = {pair.quote for pair in self.pairs}
        if len(quotes) != 1:
            raise ValueError("the initial universe requires one common quote asset")

    @property
    def quote_asset(self) -> Asset:
        return self.pairs[0].quote

    @property
    def assets(self) -> tuple[Asset, ...]:
        return tuple(pair.base for pair in self.pairs)

    def pair_for(self, asset: Asset) -> TradingPair:
        for pair in self.pairs:
            if pair.base == asset:
                return pair
        raise KeyError(asset.symbol)


@dataclass(frozen=True, slots=True)
class MarketCandle:
    pair: TradingPair
    open_time: datetime
    available_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "open_time", require_utc(self.open_time, "open_time"))
        object.__setattr__(self, "available_at", require_utc(self.available_at, "available_at"))
        if self.available_at < self.open_time:
            raise ValueError("candle cannot be available before it opens")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("invalid OHLC range")


@dataclass(frozen=True, slots=True)
class MarketDataBundle:
    universe: TradingUniverse
    candles: Mapping[str, tuple[MarketCandle, ...]]

    def __post_init__(self) -> None:
        expected = {pair.symbol for pair in self.universe.pairs}
        if not expected.issubset(self.candles):
            raise ValueError("market data is missing one or more universe pairs")
        for symbol, values in self.candles.items():
            if any(
                left.open_time >= right.open_time
                for left, right in zip(values, values[1:], strict=False)
            ):
                raise ValueError(f"candles must be strictly ordered for {symbol}")

    def known_at(self, as_of: datetime) -> "MarketDataBundle":
        cutoff = require_utc(as_of, "as_of")
        filtered = {
            symbol: tuple(candle for candle in values if candle.available_at <= cutoff)
            for symbol, values in self.candles.items()
        }
        return MarketDataBundle(self.universe, filtered)


class MarketRegime(StrEnum):
    RISK_ON = "RISK_ON"
    NEUTRAL = "NEUTRAL"
    RISK_OFF = "RISK_OFF"


@dataclass(frozen=True, slots=True)
class MarketRegimeResult:
    regime: MarketRegime
    as_of: datetime
    model: str
    evidence: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", require_utc(self.as_of, "as_of"))


def parse_trading_pair(value: str) -> TradingPair:
    normalized = value.strip().upper().replace("-", "/")
    parts = normalized.split("/")
    if len(parts) != 2:
        raise ValueError(f"pair must use BASE/QUOTE format: {value}")
    base, quote = parts
    return TradingPair(Asset(base), Asset(quote, AssetKind.CASH))
