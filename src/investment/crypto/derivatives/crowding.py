"""Point-in-time Algorithmic Crowding V1 for the BTC perpetual market."""

from __future__ import annotations

from decimal import Decimal
from math import log1p

from investment.crypto.derivatives.domain import (
    CrowdingSide,
    CrowdingSignal,
    CrowdingState,
    DerivativesSnapshot,
    SqueezeSignal,
)


class BtcAlgorithmicCrowdingCalculator:
    """Combine positioning fuel and unwind triggers without changing asset ranks."""

    feature_version = "btc-crowding-v1"

    def calculate(
        self,
        snapshot: DerivativesSnapshot,
        source_signal: SqueezeSignal,
        *,
        long_liquidation_usd_15m: float | None,
        short_liquidation_usd_15m: float | None,
        liquidation_confirmed: bool,
    ) -> CrowdingSignal:
        if snapshot.snapshot_id != source_signal.snapshot_id:
            raise ValueError("crowding source signal must match its derivatives snapshot")
        if source_signal.basis_input_rate is None:
            return self._insufficient(
                snapshot,
                long_liquidation_usd_15m,
                short_liquidation_usd_15m,
                liquidation_confirmed,
                "MISSING_BASIS_INPUT",
            )
        oi_1h = source_signal.open_interest_change_1h
        price_1h = source_signal.futures_price_change_1h
        oi_15m = source_signal.open_interest_change_15m
        price_15m = source_signal.futures_price_change_15m
        if None in (oi_1h, price_1h, oi_15m, price_15m):
            return self._insufficient(
                snapshot,
                long_liquidation_usd_15m,
                short_liquidation_usd_15m,
                liquidation_confirmed,
                "WAITING_FOR_DERIVATIVES_CHANGE_HISTORY",
            )
        assert oi_1h is not None and price_1h is not None
        assert oi_15m is not None and price_15m is not None

        basis = float(source_signal.basis_input_rate)
        funding = float(snapshot.funding_rate)
        global_ratio = float(snapshot.global_long_short_ratio)
        top_ratio = float(snapshot.top_position_long_short_ratio)
        oi_build = _positive(oi_1h, 0.03)
        short_positioning = _mean(
            _positive(1.0 - global_ratio, 0.25),
            _positive(1.0 - top_ratio, 0.25),
        )
        long_positioning = _mean(
            _positive(global_ratio - 1.0, 0.50),
            _positive(top_ratio - 1.0, 0.50),
        )
        short_score = _bounded(
            oi_build
            * (
                0.30
                + 0.20 * _positive(-price_1h, 0.02)
                + 0.20 * _positive(-funding, 0.0002)
                + 0.15 * _positive(-basis, 0.001)
                + 0.15 * short_positioning
            )
        )
        long_score = _bounded(
            oi_build
            * (
                0.30
                + 0.20 * _positive(price_1h, 0.02)
                + 0.20 * _positive(funding, 0.0002)
                + 0.15 * _positive(basis, 0.001)
                + 0.15 * long_positioning
            )
        )

        bullish_unwind = _unwind_score(
            price_move=_positive(price_15m, 0.01),
            oi_unwind=_positive(-oi_15m, 0.02),
            taker_pressure=_positive(float(snapshot.taker_buy_sell_ratio) - 1.0, 0.30),
            premium_pressure=_optional_positive(snapshot.coinbase_premium_rate, 0.001),
            liquidation_pressure=_liquidation_score(
                short_liquidation_usd_15m, liquidation_confirmed
            ),
        )
        bearish_unwind = _unwind_score(
            price_move=_positive(-price_15m, 0.01),
            oi_unwind=_positive(-oi_15m, 0.02),
            taker_pressure=_positive(1.0 - float(snapshot.taker_buy_sell_ratio), 0.30),
            premium_pressure=_optional_negative(snapshot.coinbase_premium_rate, 0.001),
            liquidation_pressure=_liquidation_score(
                long_liquidation_usd_15m, liquidation_confirmed
            ),
        )
        dominant_side = _dominant_side(long_score, short_score)
        state = _state(
            dominant_side,
            long_score,
            short_score,
            bullish_unwind,
            bearish_unwind,
        )
        confidence = 0.80
        if snapshot.coinbase_premium_rate is not None:
            confidence += 0.10
        if liquidation_confirmed:
            confidence += 0.10
        evidence = _evidence(
            state,
            long_score,
            short_score,
            bullish_unwind,
            bearish_unwind,
            snapshot.coinbase_premium_rate is not None,
            liquidation_confirmed,
        )
        return CrowdingSignal(
            snapshot_id=snapshot.snapshot_id,
            symbol=snapshot.symbol,
            as_of=snapshot.available_at,
            state=state,
            dominant_side=dominant_side,
            long_crowding_score=round(long_score, 6),
            short_crowding_score=round(short_score, 6),
            crowding_intensity=round(max(long_score, short_score), 6),
            bullish_unwind_score=round(bullish_unwind, 6),
            bearish_unwind_score=round(bearish_unwind, 6),
            confidence=round(confidence, 6),
            long_liquidation_usd_15m=long_liquidation_usd_15m,
            short_liquidation_usd_15m=short_liquidation_usd_15m,
            liquidation_confirmed=liquidation_confirmed,
            evidence=evidence,
            feature_version=self.feature_version,
        )

    def _insufficient(
        self,
        snapshot: DerivativesSnapshot,
        long_liquidation: float | None,
        short_liquidation: float | None,
        liquidation_confirmed: bool,
        reason: str,
    ) -> CrowdingSignal:
        return CrowdingSignal(
            snapshot_id=snapshot.snapshot_id,
            symbol=snapshot.symbol,
            as_of=snapshot.available_at,
            state=CrowdingState.INSUFFICIENT_DATA,
            dominant_side=CrowdingSide.NONE,
            long_crowding_score=None,
            short_crowding_score=None,
            crowding_intensity=None,
            bullish_unwind_score=None,
            bearish_unwind_score=None,
            confidence=0.0,
            long_liquidation_usd_15m=long_liquidation,
            short_liquidation_usd_15m=short_liquidation,
            liquidation_confirmed=liquidation_confirmed,
            evidence=(reason,),
            feature_version=self.feature_version,
        )


def _state(
    side: CrowdingSide,
    long_score: float,
    short_score: float,
    bullish_unwind: float,
    bearish_unwind: float,
) -> CrowdingState:
    if long_score >= 0.65 and bearish_unwind >= 0.55:
        return CrowdingState.LONG_UNWIND
    if short_score >= 0.65 and bullish_unwind >= 0.55:
        return CrowdingState.SHORT_UNWIND
    if side is CrowdingSide.LONG and long_score >= 0.65:
        return CrowdingState.LONG_CROWDED
    if side is CrowdingSide.SHORT and short_score >= 0.65:
        return CrowdingState.SHORT_CROWDED
    return CrowdingState.NEUTRAL


def _dominant_side(long_score: float, short_score: float) -> CrowdingSide:
    if max(long_score, short_score) < 0.50 or abs(long_score - short_score) < 0.10:
        return CrowdingSide.NONE
    return CrowdingSide.LONG if long_score > short_score else CrowdingSide.SHORT


def _unwind_score(
    *,
    price_move: float,
    oi_unwind: float,
    taker_pressure: float,
    premium_pressure: float | None,
    liquidation_pressure: float | None,
) -> float:
    weighted = [(0.30, price_move), (0.25, oi_unwind), (0.20, taker_pressure)]
    if premium_pressure is not None:
        weighted.append((0.10, premium_pressure))
    if liquidation_pressure is not None:
        weighted.append((0.15, liquidation_pressure))
    denominator = sum(weight for weight, _ in weighted)
    return _bounded(sum(weight * value for weight, value in weighted) / denominator)


def _liquidation_score(notional: float | None, confirmed: bool) -> float | None:
    if not confirmed or notional is None:
        return None
    return _bounded(log1p(notional) / log1p(10_000_000.0))


def _optional_positive(value: Decimal | None, scale: float) -> float | None:
    return None if value is None else _positive(float(value), scale)


def _optional_negative(value: Decimal | None, scale: float) -> float | None:
    return None if value is None else _positive(-float(value), scale)


def _positive(value: float, scale: float) -> float:
    return _bounded(value / scale)


def _mean(*values: float) -> float:
    return sum(values) / len(values)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, value))


def _evidence(
    state: CrowdingState,
    long_score: float,
    short_score: float,
    bullish_unwind: float,
    bearish_unwind: float,
    coinbase_available: bool,
    liquidation_confirmed: bool,
) -> tuple[str, ...]:
    values = [f"STATE_{state.value}"]
    if long_score >= 0.65:
        values.append("LONG_CROWDING_ELEVATED")
    if short_score >= 0.65:
        values.append("SHORT_CROWDING_ELEVATED")
    if bullish_unwind >= 0.55:
        values.append("BULLISH_UNWIND_TRIGGER")
    if bearish_unwind >= 0.55:
        values.append("BEARISH_UNWIND_TRIGGER")
    values.append(
        "COINBASE_PREMIUM_AVAILABLE" if coinbase_available else "COINBASE_PREMIUM_MISSING"
    )
    values.append(
        "LIQUIDATION_STREAM_CONFIRMED"
        if liquidation_confirmed
        else "LIQUIDATION_STREAM_UNCONFIRMED"
    )
    return tuple(values)
