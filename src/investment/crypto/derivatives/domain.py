"""Point-in-time models for BTC perpetual futures and squeeze diagnostics."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from math import isfinite

from investment.core.domain.observation import require_utc


class SqueezeState(StrEnum):
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NONE = "NONE"
    FUEL = "FUEL"
    IGNITION = "IGNITION"
    ACTIVE = "ACTIVE"


class LiquidatedPosition(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class StreamState(StrEnum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    STALE = "STALE"


class DerivativesOverlayAvailability(StrEnum):
    """Whether a stored derivatives signal was usable at a decision boundary."""

    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    STALE = "STALE"
    INCOMPLETE = "INCOMPLETE"


class DerivativesOverlayRecommendation(StrEnum):
    """Research-only interpretation of a BTC-wide derivatives observation."""

    UNKNOWN = "UNKNOWN"
    NO_CONFIRMATION = "NO_CONFIRMATION"
    WAIT_FOR_IGNITION = "WAIT_FOR_IGNITION"
    CONFIRM_RISK_ON = "CONFIRM_RISK_ON"


class BasisSource(StrEnum):
    """Provenance of the basis-like input used by a derivatives signal."""

    OFFICIAL = "OFFICIAL"
    MARK_INDEX_PROXY = "MARK_INDEX_PROXY"


class CrowdingSide(StrEnum):
    NONE = "NONE"
    LONG = "LONG"
    SHORT = "SHORT"


class CrowdingState(StrEnum):
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NEUTRAL = "NEUTRAL"
    LONG_CROWDED = "LONG_CROWDED"
    SHORT_CROWDED = "SHORT_CROWDED"
    LONG_UNWIND = "LONG_UNWIND"
    SHORT_UNWIND = "SHORT_UNWIND"


@dataclass(frozen=True, slots=True)
class LiquidationEvent:
    symbol: str
    event_time: datetime
    trade_time: datetime
    position: LiquidatedPosition
    price: Decimal
    quantity: Decimal
    source: str = "binance_usdm_force_order"

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        object.__setattr__(self, "event_time", require_utc(self.event_time, "event_time"))
        object.__setattr__(self, "trade_time", require_utc(self.trade_time, "trade_time"))
        if not self.symbol or self.price <= 0 or self.quantity <= 0:
            raise ValueError("liquidation identity, price, and quantity are required")

    @property
    def notional_usd(self) -> Decimal:
        return self.price * self.quantity

    @property
    def event_id(self) -> str:
        payload = (
            self.source,
            self.symbol,
            self.event_time.isoformat(),
            self.trade_time.isoformat(),
            self.position.value,
            str(self.price),
            str(self.quantity),
        )
        return hashlib.sha256("|".join(payload).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CoinbasePriceObservation:
    product_id: str
    observed_at: datetime
    price_usd: Decimal
    source_sequence: int
    source: str = "coinbase_advanced_ticker_batch"

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_id", self.product_id.strip().upper())
        object.__setattr__(self, "observed_at", require_utc(self.observed_at, "observed_at"))
        if not self.product_id or self.price_usd <= 0 or self.source_sequence < 0:
            raise ValueError("Coinbase price identity, value, and sequence are required")

    @property
    def observation_id(self) -> str:
        payload = (
            self.source,
            self.product_id,
            self.observed_at.isoformat(),
            str(self.price_usd),
            str(self.source_sequence),
        )
        return hashlib.sha256("|".join(payload).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class MarketStreamStatus:
    stream_name: str
    state: StreamState
    updated_at: datetime
    connected_since: datetime | None
    last_message_at: datetime | None
    last_error: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "updated_at", require_utc(self.updated_at, "updated_at"))
        for name in ("connected_since", "last_message_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, require_utc(value, name))


@dataclass(frozen=True, slots=True)
class DerivativesSnapshot:
    symbol: str
    observed_at: datetime
    available_at: datetime
    mark_price: Decimal
    index_price: Decimal
    open_interest_usd: Decimal
    funding_rate: Decimal
    basis_rate: Decimal | None
    global_long_short_ratio: Decimal
    top_position_long_short_ratio: Decimal
    taker_buy_sell_ratio: Decimal
    coinbase_price_usd: Decimal | None = None
    coinbase_premium_rate: Decimal | None = None
    coinbase_observed_at: datetime | None = None
    missing_fields: frozenset[str] = frozenset()
    source: str = "binance_usdm_futures"

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol or not symbol.isalnum():
            raise ValueError("derivatives symbol must be alphanumeric")
        object.__setattr__(self, "symbol", symbol)
        observed_at = require_utc(self.observed_at, "observed_at")
        available_at = require_utc(self.available_at, "available_at")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)
        if available_at < observed_at:
            raise ValueError("derivatives data cannot be available before it was observed")
        if (
            min(
                self.mark_price,
                self.index_price,
                self.open_interest_usd,
                self.global_long_short_ratio,
                self.top_position_long_short_ratio,
                self.taker_buy_sell_ratio,
            )
            <= 0
        ):
            raise ValueError("prices, interest, and ratios must be positive")
        if not self.source.strip():
            raise ValueError("derivatives source is required")
        if self.coinbase_observed_at is not None:
            object.__setattr__(
                self,
                "coinbase_observed_at",
                require_utc(self.coinbase_observed_at, "coinbase_observed_at"),
            )
        if (self.coinbase_price_usd is None) != (self.coinbase_premium_rate is None):
            raise ValueError("Coinbase price and premium must be available together")
        if self.coinbase_price_usd is not None and self.coinbase_price_usd <= 0:
            raise ValueError("Coinbase price must be positive")
        if (self.coinbase_price_usd is None) != (self.coinbase_observed_at is None):
            raise ValueError("Coinbase observation time must match its price")
        valid_fields = {"basis_rate", "coinbase_premium_rate"}
        if unknown := self.missing_fields - valid_fields:
            raise ValueError(f"unknown derivatives missing fields: {sorted(unknown)}")
        missing_fields = set(self.missing_fields)
        for name, value in (
            ("basis_rate", self.basis_rate),
            ("coinbase_premium_rate", self.coinbase_premium_rate),
        ):
            if value is None:
                missing_fields.add(name)
            elif name in missing_fields:
                raise ValueError(f"{name} cannot be both available and missing")
        object.__setattr__(self, "missing_fields", frozenset(missing_fields))

    @property
    def mark_index_basis_rate(self) -> Decimal:
        """Perpetual mark premium/discount over its Binance index price.

        This is a deterministic proxy derived from the two required Binance
        premium-index fields.  It is deliberately separate from ``basis_rate``:
        the latter remains the optional official `/futures/data/basis` value.
        """

        return self.mark_price / self.index_price - Decimal("1")

    @property
    def snapshot_id(self) -> str:
        payload = {
            "availableAt": self.available_at.isoformat(),
            "basisRate": None if self.basis_rate is None else str(self.basis_rate),
            "coinbaseObservedAt": (
                None if self.coinbase_observed_at is None else self.coinbase_observed_at.isoformat()
            ),
            "coinbasePremiumRate": (
                None if self.coinbase_premium_rate is None else str(self.coinbase_premium_rate)
            ),
            "coinbasePriceUsd": (
                None if self.coinbase_price_usd is None else str(self.coinbase_price_usd)
            ),
            "fundingRate": str(self.funding_rate),
            "globalLongShortRatio": str(self.global_long_short_ratio),
            "indexPrice": str(self.index_price),
            "markPrice": str(self.mark_price),
            "missingFields": sorted(self.missing_fields),
            "observedAt": self.observed_at.isoformat(),
            "openInterestUsd": str(self.open_interest_usd),
            "source": self.source,
            "symbol": self.symbol,
            "takerBuySellRatio": str(self.taker_buy_sell_ratio),
            "topPositionLongShortRatio": str(self.top_position_long_short_ratio),
        }
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SqueezeSignal:
    snapshot_id: str
    symbol: str
    as_of: datetime
    state: SqueezeState
    fuel_score: float | None
    ignition_score: float | None
    open_interest_change_15m: float | None
    open_interest_change_1h: float | None
    futures_price_change_15m: float | None
    futures_price_change_1h: float | None
    spot_return_15m: float | None
    spot_volume_ratio: float | None
    spot_breakout: bool | None
    short_liquidation_usd_15m: float | None
    liquidation_confirmed: bool
    evidence: tuple[str, ...]
    basis_input_rate: Decimal | None = None
    basis_input_source: BasisSource | None = None
    feature_version: str = "btc-squeeze-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", require_utc(self.as_of, "as_of"))
        if not self.snapshot_id or not self.symbol or not self.feature_version:
            raise ValueError("signal identity fields are required")
        for name, value in (
            ("fuel_score", self.fuel_score),
            ("ignition_score", self.ignition_score),
        ):
            if value is not None and (not isfinite(value) or not 0 <= value <= 1):
                raise ValueError(f"{name} must be in [0, 1]")
        for value in (
            self.open_interest_change_15m,
            self.open_interest_change_1h,
            self.futures_price_change_15m,
            self.futures_price_change_1h,
            self.spot_return_15m,
            self.spot_volume_ratio,
            self.short_liquidation_usd_15m,
        ):
            if value is not None and not isfinite(value):
                raise ValueError("signal metrics must be finite")
        if self.liquidation_confirmed and self.short_liquidation_usd_15m is None:
            raise ValueError("confirmed liquidation requires observed liquidation notional")
        if (self.basis_input_rate is None) != (self.basis_input_source is None):
            raise ValueError("basis input rate and source must be available together")
        if self.basis_input_rate is not None and not self.basis_input_rate.is_finite():
            raise ValueError("basis input rate must be finite")


@dataclass(frozen=True, slots=True)
class CrowdingSignal:
    """Directional BTC positioning crowding and unwind evidence.

    The score is a market-wide research feature, not a per-asset alpha score.
    Every value is derived from data already available at ``as_of``.
    """

    snapshot_id: str
    symbol: str
    as_of: datetime
    state: CrowdingState
    dominant_side: CrowdingSide
    long_crowding_score: float | None
    short_crowding_score: float | None
    crowding_intensity: float | None
    bullish_unwind_score: float | None
    bearish_unwind_score: float | None
    confidence: float
    long_liquidation_usd_15m: float | None
    short_liquidation_usd_15m: float | None
    liquidation_confirmed: bool
    evidence: tuple[str, ...]
    feature_version: str = "btc-crowding-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", require_utc(self.as_of, "as_of"))
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if not self.snapshot_id or not self.symbol or not self.feature_version or not self.evidence:
            raise ValueError("crowding signal identity and evidence are required")
        for name, value in (
            ("long_crowding_score", self.long_crowding_score),
            ("short_crowding_score", self.short_crowding_score),
            ("crowding_intensity", self.crowding_intensity),
            ("bullish_unwind_score", self.bullish_unwind_score),
            ("bearish_unwind_score", self.bearish_unwind_score),
            ("confidence", self.confidence),
        ):
            if value is not None and (not isfinite(value) or not 0 <= value <= 1):
                raise ValueError(f"{name} must be in [0, 1]")
        for value in (self.long_liquidation_usd_15m, self.short_liquidation_usd_15m):
            if value is not None and (not isfinite(value) or value < 0):
                raise ValueError("liquidation notionals must be finite and non-negative")
        if self.liquidation_confirmed and (
            self.long_liquidation_usd_15m is None
            or self.short_liquidation_usd_15m is None
        ):
            raise ValueError("confirmed coverage requires both liquidation notionals")
        if self.state is CrowdingState.INSUFFICIENT_DATA:
            if self.dominant_side is not CrowdingSide.NONE:
                raise ValueError("insufficient crowding data cannot select a dominant side")
        elif None in (
            self.long_crowding_score,
            self.short_crowding_score,
            self.crowding_intensity,
            self.bullish_unwind_score,
            self.bearish_unwind_score,
        ):
            raise ValueError("complete crowding states require every score")


@dataclass(frozen=True, slots=True)
class BtcDerivativesDecisionOverlay:
    """Immutable BTC market context that was knowable at one strategy decision.

    This is deliberately a market-wide context rather than a per-asset score.  A
    consumer may attach the same object to every candidate in an audit record,
    but must not reinterpret missing values as zero or fetch a newer observation
    after the decision.
    """

    decision_as_of: datetime
    availability: DerivativesOverlayAvailability
    recommendation: DerivativesOverlayRecommendation
    reason_codes: tuple[str, ...]
    symbol: str = "BTCUSDT"
    snapshot_id: str | None = None
    snapshot_available_at: datetime | None = None
    signal_as_of: datetime | None = None
    age_seconds: float | None = None
    feature_version: str | None = None
    state: SqueezeState | None = None
    fuel_score: float | None = None
    ignition_score: float | None = None
    open_interest_change_15m: float | None = None
    open_interest_change_1h: float | None = None
    futures_price_change_15m: float | None = None
    futures_price_change_1h: float | None = None
    spot_return_15m: float | None = None
    spot_volume_ratio: float | None = None
    spot_breakout: bool | None = None
    short_liquidation_usd_15m: float | None = None
    liquidation_confirmed: bool | None = None
    mark_price: Decimal | None = None
    index_price: Decimal | None = None
    open_interest_usd: Decimal | None = None
    funding_rate: Decimal | None = None
    basis_rate: Decimal | None = None
    mark_index_basis_rate: Decimal | None = None
    basis_input_rate: Decimal | None = None
    basis_input_source: BasisSource | None = None
    global_long_short_ratio: Decimal | None = None
    top_position_long_short_ratio: Decimal | None = None
    taker_buy_sell_ratio: Decimal | None = None
    coinbase_price_usd: Decimal | None = None
    coinbase_premium_rate: Decimal | None = None
    coinbase_observed_at: datetime | None = None
    missing_fields: frozenset[str] = frozenset()
    crowding_availability: DerivativesOverlayAvailability | None = None
    crowding_snapshot_id: str | None = None
    crowding_signal_as_of: datetime | None = None
    crowding_age_seconds: float | None = None
    crowding_feature_version: str | None = None
    crowding_state: CrowdingState | None = None
    crowding_dominant_side: CrowdingSide | None = None
    long_crowding_score: float | None = None
    short_crowding_score: float | None = None
    crowding_intensity: float | None = None
    bullish_unwind_score: float | None = None
    bearish_unwind_score: float | None = None
    crowding_confidence: float | None = None
    long_liquidation_usd_15m: float | None = None

    def __post_init__(self) -> None:
        decision_as_of = require_utc(self.decision_as_of, "decision_as_of")
        object.__setattr__(self, "decision_as_of", decision_as_of)
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if not self.symbol or not self.reason_codes:
            raise ValueError("overlay symbol and reason codes are required")
        if self.signal_as_of is not None:
            signal_as_of = require_utc(self.signal_as_of, "signal_as_of")
            object.__setattr__(self, "signal_as_of", signal_as_of)
            if signal_as_of > decision_as_of:
                raise ValueError("derivatives overlay cannot use a future signal")
        if self.snapshot_available_at is not None:
            snapshot_available_at = require_utc(self.snapshot_available_at, "snapshot_available_at")
            object.__setattr__(self, "snapshot_available_at", snapshot_available_at)
            if snapshot_available_at > decision_as_of:
                raise ValueError("derivatives overlay cannot use a future snapshot")
        if self.coinbase_observed_at is not None:
            object.__setattr__(
                self,
                "coinbase_observed_at",
                require_utc(self.coinbase_observed_at, "coinbase_observed_at"),
            )
        if self.age_seconds is not None and (
            not isfinite(self.age_seconds) or self.age_seconds < 0
        ):
            raise ValueError("overlay age must be a finite non-negative number")
        if (self.basis_input_rate is None) != (self.basis_input_source is None):
            raise ValueError("overlay basis input rate and source must be available together")
        has_observation = self.snapshot_id is not None
        if self.availability is DerivativesOverlayAvailability.MISSING and has_observation:
            raise ValueError("a missing overlay cannot identify a snapshot")
        if self.availability is not DerivativesOverlayAvailability.MISSING and not has_observation:
            raise ValueError("a non-missing overlay must identify a snapshot")
        if has_observation and (
            self.snapshot_available_at is None
            or self.signal_as_of is None
            or self.age_seconds is None
            or self.feature_version is None
            or self.state is None
            or self.liquidation_confirmed is None
        ):
            raise ValueError("observed overlays require signal identity and state")
        if (
            self.availability is not DerivativesOverlayAvailability.AVAILABLE
            and self.recommendation is not DerivativesOverlayRecommendation.UNKNOWN
        ):
            raise ValueError("unavailable overlays cannot make a market recommendation")
        if self.crowding_signal_as_of is not None:
            crowding_as_of = require_utc(self.crowding_signal_as_of, "crowding_signal_as_of")
            object.__setattr__(self, "crowding_signal_as_of", crowding_as_of)
            if crowding_as_of > decision_as_of:
                raise ValueError("crowding overlay cannot use a future signal")
        if self.crowding_age_seconds is not None and (
            not isfinite(self.crowding_age_seconds) or self.crowding_age_seconds < 0
        ):
            raise ValueError("crowding age must be finite and non-negative")

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-safe audit payload."""

        return {
            "decisionAsOf": self.decision_as_of.isoformat(),
            "availability": self.availability.value,
            "recommendation": self.recommendation.value,
            "reasonCodes": list(self.reason_codes),
            "symbol": self.symbol,
            "snapshotId": self.snapshot_id,
            "snapshotAvailableAt": (
                self.snapshot_available_at.isoformat() if self.snapshot_available_at else None
            ),
            "signalAsOf": self.signal_as_of.isoformat() if self.signal_as_of else None,
            "ageSeconds": self.age_seconds,
            "featureVersion": self.feature_version,
            "state": self.state.value if self.state else None,
            "fuelScore": self.fuel_score,
            "ignitionScore": self.ignition_score,
            "openInterestChange15m": self.open_interest_change_15m,
            "openInterestChange1h": self.open_interest_change_1h,
            "futuresPriceChange15m": self.futures_price_change_15m,
            "futuresPriceChange1h": self.futures_price_change_1h,
            "spotReturn15m": self.spot_return_15m,
            "spotVolumeRatio": self.spot_volume_ratio,
            "spotBreakout": self.spot_breakout,
            "shortLiquidationUsd15m": self.short_liquidation_usd_15m,
            "liquidationConfirmed": self.liquidation_confirmed,
            "markPrice": _decimal_text(self.mark_price),
            "indexPrice": _decimal_text(self.index_price),
            "openInterestUsd": _decimal_text(self.open_interest_usd),
            "fundingRate": _decimal_text(self.funding_rate),
            "basisRate": _decimal_text(self.basis_rate),
            "markIndexBasisRate": _decimal_text(self.mark_index_basis_rate),
            "basisInputRate": _decimal_text(self.basis_input_rate),
            "basisInputSource": (
                None if self.basis_input_source is None else self.basis_input_source.value
            ),
            "globalLongShortRatio": _decimal_text(self.global_long_short_ratio),
            "topPositionLongShortRatio": _decimal_text(self.top_position_long_short_ratio),
            "takerBuySellRatio": _decimal_text(self.taker_buy_sell_ratio),
            "coinbasePriceUsd": _decimal_text(self.coinbase_price_usd),
            "coinbasePremiumRate": _decimal_text(self.coinbase_premium_rate),
            "coinbaseObservedAt": (
                self.coinbase_observed_at.isoformat() if self.coinbase_observed_at else None
            ),
            "missingFields": sorted(self.missing_fields),
            "crowdingAvailability": (
                None if self.crowding_availability is None else self.crowding_availability.value
            ),
            "crowdingSnapshotId": self.crowding_snapshot_id,
            "crowdingSignalAsOf": (
                None
                if self.crowding_signal_as_of is None
                else self.crowding_signal_as_of.isoformat()
            ),
            "crowdingAgeSeconds": self.crowding_age_seconds,
            "crowdingFeatureVersion": self.crowding_feature_version,
            "crowdingState": None if self.crowding_state is None else self.crowding_state.value,
            "crowdingDominantSide": (
                None if self.crowding_dominant_side is None else self.crowding_dominant_side.value
            ),
            "longCrowdingScore": self.long_crowding_score,
            "shortCrowdingScore": self.short_crowding_score,
            "crowdingIntensity": self.crowding_intensity,
            "bullishUnwindScore": self.bullish_unwind_score,
            "bearishUnwindScore": self.bearish_unwind_score,
            "crowdingConfidence": self.crowding_confidence,
            "longLiquidationUsd15m": self.long_liquidation_usd_15m,
        }


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
