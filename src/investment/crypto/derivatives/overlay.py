"""Point-in-time BTC derivatives context for strategy decision auditing."""

from datetime import datetime, timedelta
from typing import Protocol

from investment.core.domain.observation import require_utc
from investment.crypto.derivatives.domain import (
    BtcDerivativesDecisionOverlay,
    CrowdingSignal,
    CrowdingState,
    DerivativesOverlayAvailability,
    DerivativesOverlayRecommendation,
    DerivativesSnapshot,
    SqueezeSignal,
    SqueezeState,
)
from investment.crypto.derivatives.repository import SqliteDerivativesObservationRepository


class BtcDerivativesOverlayProvider(Protocol):
    """Read-only strategy port; implementations must never fetch live data here."""

    def known_at(
        self,
        as_of: datetime,
        *,
        feature_version: str,
        maximum_age: timedelta,
        crowding_feature_version: str | None = None,
        crowding_maximum_age: timedelta | None = None,
    ) -> BtcDerivativesDecisionOverlay: ...


class PointInTimeBtcDerivativesOverlayProvider:
    """Build a decision overlay exclusively from already persisted observations."""

    def __init__(
        self,
        repository: SqliteDerivativesObservationRepository,
        symbol: str = "BTCUSDT",
    ) -> None:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("overlay symbol is required")
        self._repository = repository
        self._symbol = normalized_symbol

    def known_at(
        self,
        as_of: datetime,
        *,
        feature_version: str,
        maximum_age: timedelta,
        crowding_feature_version: str | None = None,
        crowding_maximum_age: timedelta | None = None,
    ) -> BtcDerivativesDecisionOverlay:
        cutoff = require_utc(as_of, "as_of")
        normalized_version = feature_version.strip()
        if not normalized_version:
            raise ValueError("overlay feature version is required")
        if maximum_age <= timedelta(0):
            raise ValueError("overlay maximum age must be positive")
        if crowding_feature_version is not None and (
            not crowding_feature_version.strip()
            or crowding_maximum_age is None
            or crowding_maximum_age <= timedelta(0)
        ):
            raise ValueError("crowding feature version and positive maximum age are required")
        observation = self._repository.observation_known_at(
            self._symbol,
            cutoff,
            normalized_version,
        )
        if observation is None:
            return BtcDerivativesDecisionOverlay(
                decision_as_of=cutoff,
                availability=DerivativesOverlayAvailability.MISSING,
                recommendation=DerivativesOverlayRecommendation.UNKNOWN,
                reason_codes=("NO_COMPATIBLE_DERIVATIVES_SIGNAL",),
                symbol=self._symbol,
                feature_version=normalized_version,
            )

        snapshot, signal = observation
        effective_available_at = max(snapshot.available_at, signal.as_of)
        age_seconds = (cutoff - effective_available_at).total_seconds()
        reasons: tuple[str, ...]
        if age_seconds > maximum_age.total_seconds():
            availability = DerivativesOverlayAvailability.STALE
            recommendation = DerivativesOverlayRecommendation.UNKNOWN
            reasons = ("DERIVATIVES_SIGNAL_STALE",)
        elif signal.state is SqueezeState.INSUFFICIENT_DATA:
            availability = DerivativesOverlayAvailability.INCOMPLETE
            recommendation = DerivativesOverlayRecommendation.UNKNOWN
            reasons = tuple(dict.fromkeys(("DERIVATIVES_SIGNAL_INCOMPLETE", *signal.evidence)))
        else:
            availability = DerivativesOverlayAvailability.AVAILABLE
            recommendation, primary_reason = _recommendation(signal.state)
            reasons = tuple(dict.fromkeys((primary_reason, *signal.evidence)))

        optional_reasons = []
        if "coinbase_premium_rate" in snapshot.missing_fields:
            optional_reasons.append("OPTIONAL_COINBASE_PREMIUM_MISSING")
        if not signal.liquidation_confirmed:
            optional_reasons.append("LIQUIDATION_COVERAGE_UNCONFIRMED")
        reasons = tuple(dict.fromkeys((*reasons, *optional_reasons)))
        crowding = (
            self._repository.crowding_known_at(
                self._symbol, cutoff, crowding_feature_version
            )
            if crowding_feature_version is not None
            else None
        )
        return _overlay(
            cutoff,
            snapshot,
            signal,
            age_seconds,
            availability,
            recommendation,
            reasons,
            crowding,
            crowding_maximum_age,
        )


def _recommendation(
    state: SqueezeState,
) -> tuple[DerivativesOverlayRecommendation, str]:
    if state is SqueezeState.NONE:
        return (
            DerivativesOverlayRecommendation.NO_CONFIRMATION,
            "NO_DERIVATIVES_RISK_ON_CONFIRMATION",
        )
    if state is SqueezeState.FUEL:
        return DerivativesOverlayRecommendation.WAIT_FOR_IGNITION, "SHORT_FUEL_WAITING_FOR_IGNITION"
    if state in {SqueezeState.IGNITION, SqueezeState.ACTIVE}:
        return DerivativesOverlayRecommendation.CONFIRM_RISK_ON, "DERIVATIVES_RISK_ON_CONFIRMED"
    return DerivativesOverlayRecommendation.UNKNOWN, "DERIVATIVES_SIGNAL_INCOMPLETE"


def _overlay(
    decision_as_of: datetime,
    snapshot: DerivativesSnapshot,
    signal: SqueezeSignal,
    age_seconds: float,
    availability: DerivativesOverlayAvailability,
    recommendation: DerivativesOverlayRecommendation,
    reason_codes: tuple[str, ...],
    crowding_observation: tuple[DerivativesSnapshot, CrowdingSignal] | None = None,
    crowding_maximum_age: timedelta | None = None,
) -> BtcDerivativesDecisionOverlay:
    crowding_signal = None if crowding_observation is None else crowding_observation[1]
    crowding_availability = None
    crowding_age_seconds = None
    if crowding_maximum_age is not None:
        if crowding_signal is None:
            crowding_availability = DerivativesOverlayAvailability.MISSING
        elif decision_as_of - crowding_signal.as_of > crowding_maximum_age:
            crowding_availability = DerivativesOverlayAvailability.STALE
        elif crowding_signal.state is CrowdingState.INSUFFICIENT_DATA:
            crowding_availability = DerivativesOverlayAvailability.INCOMPLETE
        else:
            crowding_availability = DerivativesOverlayAvailability.AVAILABLE
        if crowding_signal is not None:
            crowding_age_seconds = (decision_as_of - crowding_signal.as_of).total_seconds()
    return BtcDerivativesDecisionOverlay(
        decision_as_of=decision_as_of,
        availability=availability,
        recommendation=recommendation,
        reason_codes=reason_codes,
        symbol=snapshot.symbol,
        snapshot_id=snapshot.snapshot_id,
        snapshot_available_at=snapshot.available_at,
        signal_as_of=signal.as_of,
        age_seconds=age_seconds,
        feature_version=signal.feature_version,
        state=signal.state,
        fuel_score=signal.fuel_score,
        ignition_score=signal.ignition_score,
        open_interest_change_15m=signal.open_interest_change_15m,
        open_interest_change_1h=signal.open_interest_change_1h,
        futures_price_change_15m=signal.futures_price_change_15m,
        futures_price_change_1h=signal.futures_price_change_1h,
        spot_return_15m=signal.spot_return_15m,
        spot_volume_ratio=signal.spot_volume_ratio,
        spot_breakout=signal.spot_breakout,
        short_liquidation_usd_15m=signal.short_liquidation_usd_15m,
        liquidation_confirmed=signal.liquidation_confirmed,
        mark_price=snapshot.mark_price,
        index_price=snapshot.index_price,
        open_interest_usd=snapshot.open_interest_usd,
        funding_rate=snapshot.funding_rate,
        basis_rate=snapshot.basis_rate,
        mark_index_basis_rate=snapshot.mark_index_basis_rate,
        basis_input_rate=signal.basis_input_rate,
        basis_input_source=signal.basis_input_source,
        global_long_short_ratio=snapshot.global_long_short_ratio,
        top_position_long_short_ratio=snapshot.top_position_long_short_ratio,
        taker_buy_sell_ratio=snapshot.taker_buy_sell_ratio,
        coinbase_price_usd=snapshot.coinbase_price_usd,
        coinbase_premium_rate=snapshot.coinbase_premium_rate,
        coinbase_observed_at=snapshot.coinbase_observed_at,
        missing_fields=snapshot.missing_fields,
        crowding_availability=crowding_availability,
        crowding_snapshot_id=(
            None if crowding_signal is None else crowding_signal.snapshot_id
        ),
        crowding_signal_as_of=(
            None if crowding_signal is None else crowding_signal.as_of
        ),
        crowding_age_seconds=crowding_age_seconds,
        crowding_feature_version=(
            None if crowding_signal is None else crowding_signal.feature_version
        ),
        crowding_state=None if crowding_signal is None else crowding_signal.state,
        crowding_dominant_side=(
            None if crowding_signal is None else crowding_signal.dominant_side
        ),
        long_crowding_score=(
            None if crowding_signal is None else crowding_signal.long_crowding_score
        ),
        short_crowding_score=(
            None if crowding_signal is None else crowding_signal.short_crowding_score
        ),
        crowding_intensity=(
            None if crowding_signal is None else crowding_signal.crowding_intensity
        ),
        bullish_unwind_score=(
            None if crowding_signal is None else crowding_signal.bullish_unwind_score
        ),
        bearish_unwind_score=(
            None if crowding_signal is None else crowding_signal.bearish_unwind_score
        ),
        crowding_confidence=(
            None if crowding_signal is None else crowding_signal.confidence
        ),
        long_liquidation_usd_15m=(
            None if crowding_signal is None else crowding_signal.long_liquidation_usd_15m
        ),
    )
