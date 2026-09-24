from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment.crypto.derivatives.crowding import BtcAlgorithmicCrowdingCalculator
from investment.crypto.derivatives.domain import (
    BasisSource,
    CrowdingSide,
    CrowdingState,
    DerivativesSnapshot,
    SqueezeSignal,
    SqueezeState,
)
from investment.crypto.derivatives.repository import SqliteDerivativesObservationRepository


def _snapshot(at: datetime) -> DerivativesSnapshot:
    return DerivativesSnapshot(
        symbol="BTCUSDT",
        observed_at=at,
        available_at=at,
        mark_price=Decimal("98000"),
        index_price=Decimal("97902.09790209790209790209790"),
        open_interest_usd=Decimal("10000000000"),
        funding_rate=Decimal("0.0003"),
        basis_rate=None,
        global_long_short_ratio=Decimal("1.8"),
        top_position_long_short_ratio=Decimal("1.7"),
        taker_buy_sell_ratio=Decimal("0.5"),
        coinbase_price_usd=Decimal("97706.29370629370629370629371"),
        coinbase_premium_rate=Decimal("-0.002"),
        coinbase_observed_at=at,
    )


def _source(snapshot: DerivativesSnapshot) -> SqueezeSignal:
    return SqueezeSignal(
        snapshot_id=snapshot.snapshot_id,
        symbol=snapshot.symbol,
        as_of=snapshot.available_at,
        state=SqueezeState.NONE,
        fuel_score=0.1,
        ignition_score=0.1,
        open_interest_change_15m=-0.03,
        open_interest_change_1h=0.04,
        futures_price_change_15m=-0.02,
        futures_price_change_1h=0.02,
        spot_return_15m=-0.02,
        spot_volume_ratio=2.0,
        spot_breakout=False,
        short_liquidation_usd_15m=0.0,
        liquidation_confirmed=True,
        evidence=("BASIS_INPUT_MARK_INDEX_PROXY",),
        basis_input_rate=Decimal("0.001"),
        basis_input_source=BasisSource.MARK_INDEX_PROXY,
        feature_version="btc-squeeze-v3-mark-index-basis-proxy",
    )


def test_crowding_v1_detects_long_crowding_with_bearish_unwind() -> None:
    at = datetime(2026, 9, 1, tzinfo=UTC)
    snapshot = _snapshot(at)

    signal = BtcAlgorithmicCrowdingCalculator().calculate(
        snapshot,
        _source(snapshot),
        long_liquidation_usd_15m=10_000_000.0,
        short_liquidation_usd_15m=0.0,
        liquidation_confirmed=True,
    )

    assert signal.feature_version == "btc-crowding-v1"
    assert signal.state is CrowdingState.LONG_UNWIND
    assert signal.dominant_side is CrowdingSide.LONG
    assert signal.long_crowding_score == 1.0
    assert signal.bearish_unwind_score == 1.0
    assert signal.confidence == 1.0


def test_crowding_signal_is_persisted_and_point_in_time_safe(tmp_path: Path) -> None:
    at = datetime(2026, 9, 1, tzinfo=UTC)
    snapshot = _snapshot(at)
    signal = BtcAlgorithmicCrowdingCalculator().calculate(
        snapshot,
        _source(snapshot),
        long_liquidation_usd_15m=10_000_000.0,
        short_liquidation_usd_15m=0.0,
        liquidation_confirmed=True,
    )
    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    repository.save_snapshot(snapshot)
    repository.save_crowding_signal(signal)

    assert repository.crowding_known_at(
        "BTCUSDT", at - timedelta(microseconds=1), signal.feature_version
    ) is None
    known = repository.crowding_known_at(
        "BTCUSDT", at + timedelta(minutes=1), signal.feature_version
    )
    assert known is not None
    assert known[0].snapshot_id == snapshot.snapshot_id
    assert known[1] == signal
