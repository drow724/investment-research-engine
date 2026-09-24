import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from investment.crypto.derivatives.domain import (
    BasisSource,
    BtcDerivativesDecisionOverlay,
    DerivativesOverlayAvailability,
    DerivativesOverlayRecommendation,
    DerivativesSnapshot,
    SqueezeSignal,
    SqueezeState,
)
from investment.crypto.derivatives.overlay import PointInTimeBtcDerivativesOverlayProvider
from investment.crypto.derivatives.repository import SqliteDerivativesObservationRepository
from investment.crypto.derivatives.service import BtcSqueezeBasisProxySignalCalculator

FEATURE_VERSION = "btc-squeeze-v2-market-streams"


def test_repository_known_at_excludes_future_and_wrong_feature_versions(tmp_path) -> None:
    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    first_at = datetime(2026, 1, 1, 10, 3, tzinfo=UTC)
    future_at = first_at + timedelta(minutes=15)
    first = _snapshot(first_at)
    future = _snapshot(future_at)
    for snapshot in (first, future):
        repository.save_snapshot(snapshot)
        repository.save_signal(_signal(snapshot, SqueezeState.NONE))
    repository.save_signal(
        _signal(future, SqueezeState.FUEL, feature_version="btc-squeeze-experimental")
    )

    assert (
        repository.observation_known_at("btcusdt", first_at - timedelta(seconds=1), FEATURE_VERSION)
        is None
    )
    known = repository.observation_known_at(
        "btcusdt", first_at + timedelta(minutes=2), FEATURE_VERSION
    )
    assert known is not None
    assert known[0] == first
    assert known[1].snapshot_id == first.snapshot_id
    assert (
        repository.observation_known_at(
            "BTCUSDT", first_at + timedelta(minutes=2), "btc-squeeze-experimental"
        )
        is None
    )
    experimental = repository.observation_known_at("BTCUSDT", future_at, "btc-squeeze-experimental")
    assert experimental is not None
    assert experimental[0] == future


def test_overlay_reports_missing_without_falling_through_to_latest(tmp_path) -> None:
    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    future_at = datetime(2026, 1, 1, 10, 18, tzinfo=UTC)
    future = _snapshot(future_at)
    repository.save_snapshot(future)
    repository.save_signal(_signal(future, SqueezeState.IGNITION))
    provider = PointInTimeBtcDerivativesOverlayProvider(repository)

    overlay = provider.known_at(
        future_at - timedelta(minutes=1),
        feature_version=FEATURE_VERSION,
        maximum_age=timedelta(minutes=10),
    )

    assert overlay.availability is DerivativesOverlayAvailability.MISSING
    assert overlay.recommendation is DerivativesOverlayRecommendation.UNKNOWN
    assert overlay.snapshot_id is None
    assert overlay.feature_version == FEATURE_VERSION
    assert overlay.reason_codes == ("NO_COMPATIBLE_DERIVATIVES_SIGNAL",)


@pytest.mark.parametrize(
    ("state", "recommendation"),
    [
        (SqueezeState.NONE, DerivativesOverlayRecommendation.NO_CONFIRMATION),
        (SqueezeState.FUEL, DerivativesOverlayRecommendation.WAIT_FOR_IGNITION),
        (SqueezeState.IGNITION, DerivativesOverlayRecommendation.CONFIRM_RISK_ON),
        (SqueezeState.ACTIVE, DerivativesOverlayRecommendation.CONFIRM_RISK_ON),
    ],
)
def test_fresh_overlay_preserves_market_wide_signal_and_optional_gaps(
    tmp_path,
    state: SqueezeState,
    recommendation: DerivativesOverlayRecommendation,
) -> None:
    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    signal_at = datetime(2026, 1, 1, 10, 3, tzinfo=UTC)
    snapshot = _snapshot(signal_at)
    repository.save_snapshot(snapshot)
    repository.save_signal(_signal(snapshot, state))
    provider = PointInTimeBtcDerivativesOverlayProvider(repository)

    overlay = provider.known_at(
        signal_at + timedelta(minutes=10),
        feature_version=FEATURE_VERSION,
        maximum_age=timedelta(minutes=10),
    )

    assert overlay.availability is DerivativesOverlayAvailability.AVAILABLE
    assert overlay.recommendation is recommendation
    assert overlay.snapshot_id == snapshot.snapshot_id
    assert overlay.snapshot_available_at == signal_at
    assert overlay.signal_as_of == signal_at
    assert overlay.age_seconds == 600
    assert overlay.funding_rate == Decimal("-0.0001")
    assert overlay.coinbase_premium_rate is None
    assert "OPTIONAL_COINBASE_PREMIUM_MISSING" in overlay.reason_codes
    assert ("LIQUIDATION_COVERAGE_UNCONFIRMED" in overlay.reason_codes) == (
        state is not SqueezeState.ACTIVE
    )
    json.dumps(overlay.to_dict(), allow_nan=False)


def test_overlay_marks_observation_stale_after_strict_age_boundary(tmp_path) -> None:
    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    signal_at = datetime(2026, 1, 1, 10, 3, tzinfo=UTC)
    snapshot = _snapshot(signal_at)
    repository.save_snapshot(snapshot)
    repository.save_signal(_signal(snapshot, SqueezeState.ACTIVE))
    provider = PointInTimeBtcDerivativesOverlayProvider(repository)

    overlay = provider.known_at(
        signal_at + timedelta(minutes=10, microseconds=1),
        feature_version=FEATURE_VERSION,
        maximum_age=timedelta(minutes=10),
    )

    assert overlay.availability is DerivativesOverlayAvailability.STALE
    assert overlay.recommendation is DerivativesOverlayRecommendation.UNKNOWN
    assert overlay.reason_codes[0] == "DERIVATIVES_SIGNAL_STALE"


def test_missing_basis_is_incomplete_and_is_never_imputed(tmp_path) -> None:
    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    signal_at = datetime(2026, 1, 1, 10, 3, tzinfo=UTC)
    snapshot = replace(
        _snapshot(signal_at),
        basis_rate=None,
        missing_fields=frozenset({"basis_rate"}),
    )
    repository.save_snapshot(snapshot)
    repository.save_signal(_signal(snapshot, SqueezeState.INSUFFICIENT_DATA))
    provider = PointInTimeBtcDerivativesOverlayProvider(repository)

    overlay = provider.known_at(
        signal_at + timedelta(minutes=2),
        feature_version=FEATURE_VERSION,
        maximum_age=timedelta(minutes=10),
    )

    assert overlay.availability is DerivativesOverlayAvailability.INCOMPLETE
    assert overlay.recommendation is DerivativesOverlayRecommendation.UNKNOWN
    assert overlay.basis_rate is None
    assert "basis_rate" in overlay.missing_fields
    assert "MISSING_DATA_BASIS_RATE" in overlay.reason_codes
    assert overlay.to_dict()["basisRate"] is None


def test_proxy_basis_is_explicit_and_usable_only_in_the_proxy_feature_version(tmp_path) -> None:
    repository = SqliteDerivativesObservationRepository(tmp_path / "derivatives.sqlite3")
    signal_at = datetime(2026, 1, 1, 10, 3, tzinfo=UTC)
    snapshots = tuple(
        replace(
            _snapshot(signal_at - timedelta(minutes=15 * (4 - index))),
            basis_rate=None,
            missing_fields=frozenset({"basis_rate"}),
        )
        for index in range(5)
    )
    for snapshot in snapshots:
        repository.save_snapshot(snapshot)
    signal = BtcSqueezeBasisProxySignalCalculator().calculate(snapshots)
    repository.save_signal(signal)
    provider = PointInTimeBtcDerivativesOverlayProvider(repository)

    overlay = provider.known_at(
        signal_at + timedelta(minutes=2),
        feature_version="btc-squeeze-v3-mark-index-basis-proxy",
        maximum_age=timedelta(minutes=10),
    )

    assert overlay.availability is DerivativesOverlayAvailability.AVAILABLE
    assert overlay.basis_rate is None
    assert overlay.mark_index_basis_rate == Decimal("100000") / Decimal("99900") - 1
    assert overlay.basis_input_source is BasisSource.MARK_INDEX_PROXY
    assert overlay.to_dict()["basisInputSource"] == "MARK_INDEX_PROXY"


def test_overlay_rejects_an_unpaired_basis_input_provenance() -> None:
    with pytest.raises(ValueError, match="basis input rate and source"):
        BtcDerivativesDecisionOverlay(
            decision_as_of=datetime(2026, 1, 1, tzinfo=UTC),
            availability=DerivativesOverlayAvailability.MISSING,
            recommendation=DerivativesOverlayRecommendation.UNKNOWN,
            reason_codes=("NO_COMPATIBLE_DERIVATIVES_SIGNAL",),
            basis_input_rate=Decimal("0.001"),
        )


def _snapshot(as_of: datetime) -> DerivativesSnapshot:
    return DerivativesSnapshot(
        symbol="BTCUSDT",
        observed_at=as_of,
        available_at=as_of,
        mark_price=Decimal("100000"),
        index_price=Decimal("99900"),
        open_interest_usd=Decimal("100000000"),
        funding_rate=Decimal("-0.0001"),
        basis_rate=Decimal("-0.001"),
        global_long_short_ratio=Decimal("0.8"),
        top_position_long_short_ratio=Decimal("0.85"),
        taker_buy_sell_ratio=Decimal("1.2"),
    )


def _signal(
    snapshot: DerivativesSnapshot,
    state: SqueezeState,
    *,
    feature_version: str = FEATURE_VERSION,
) -> SqueezeSignal:
    incomplete = state is SqueezeState.INSUFFICIENT_DATA
    active = state is SqueezeState.ACTIVE
    evidence = (
        ("MISSING_DATA_BASIS_RATE",)
        if incomplete
        else ("NO_SQUEEZE_SETUP",)
        if state is SqueezeState.NONE
        else ("SHORT_FUEL_CONDITIONS_PRESENT",)
    )
    return SqueezeSignal(
        snapshot_id=snapshot.snapshot_id,
        symbol=snapshot.symbol,
        as_of=snapshot.available_at,
        state=state,
        fuel_score=None if incomplete else 0.6,
        ignition_score=None if incomplete else 0.7,
        open_interest_change_15m=None if incomplete else -0.01,
        open_interest_change_1h=None if incomplete else 0.03,
        futures_price_change_15m=None if incomplete else 0.01,
        futures_price_change_1h=None if incomplete else -0.005,
        spot_return_15m=None if incomplete else 0.01,
        spot_volume_ratio=None if incomplete else 2.0,
        spot_breakout=None if incomplete else True,
        short_liquidation_usd_15m=1000.0 if active else None,
        liquidation_confirmed=active,
        evidence=evidence,
        feature_version=feature_version,
    )
