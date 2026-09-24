from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest

from investment.crypto.application.dynamic_paper_rebalance import (
    V28_STRATEGY_VERSIONS,
    DynamicPaperRebalanceService,
    dynamic_policy_for_version,
)
from investment.crypto.derivatives.domain import (
    BtcDerivativesDecisionOverlay,
    DerivativesOverlayAvailability,
    DerivativesOverlayRecommendation,
    SqueezeState,
)
from investment.crypto.strategy_registry import StrategyRegistry


def service(suffix):
    return DynamicPaperRebalanceService(
        Mock(),
        Mock(),
        Mock(),
        Mock(),
        dynamic_policy_for_version(f"dynamic-intraday-v2.8-{suffix}"),
    )


def overlay(**changes):
    value = BtcDerivativesDecisionOverlay(
        decision_as_of=datetime(2026, 9, 9, tzinfo=UTC),
        availability=DerivativesOverlayAvailability.AVAILABLE,
        recommendation=DerivativesOverlayRecommendation.NO_CONFIRMATION,
        reason_codes=("TEST",),
        snapshot_id="test",
        snapshot_available_at=datetime(2026, 9, 9, tzinfo=UTC),
        signal_as_of=datetime(2026, 9, 9, tzinfo=UTC),
        feature_version="test",
        state=SqueezeState.NONE,
        age_seconds=0,
        liquidation_confirmed=True,
        basis_input_source="MARK_INDEX_PROXY",
        funding_rate=Decimal("0"),
        basis_input_rate=Decimal("-0.001"),
        global_long_short_ratio=Decimal("1.2"),
    )
    return replace(value, **changes)


def test_v28_registry_preserves_baseline_and_all_profiles_roundtrip():
    baseline = dynamic_policy_for_version("dynamic-intraday-v2.7-accuracy-v1")
    control = dynamic_policy_for_version(V28_STRATEGY_VERSIONS[0])
    assert replace(control, strategy_version=baseline.strategy_version) == baseline
    for version in V28_STRATEGY_VERSIONS:
        assert StrategyRegistry().load(version) == dynamic_policy_for_version(version)
    assert StrategyRegistry().load(baseline.strategy_version) == baseline


def test_long_short_gate_does_not_use_funding_or_basis_as_directional_gate():
    assert service("production-control")._raw_derivatives_entry_block_reason(overlay())
    candidate = service("long-short")
    assert candidate._raw_derivatives_entry_block_reason(overlay()) is None
    assert (
        candidate._raw_derivatives_entry_block_reason(
            overlay(global_long_short_ratio=Decimal("0.9"))
        )
        == "NEW_ENTRY_BLOCKED_BY_LOW_BTC_LONG_SHORT_REGIME"
    )


def test_soft_penalty_is_bounded_and_missing_is_not_neutral():
    candidate = service("soft-penalty")
    assert candidate._raw_derivatives_entry_block_reason(overlay()) is None
    assert candidate._derivatives_cost_penalty(overlay()) == pytest.approx(0.001)
    assert candidate._derivatives_cost_penalty(
        overlay(global_long_short_ratio=Decimal("0.8"))
    ) == pytest.approx(0.0015)
    for value in (
        None,
        overlay(
            availability=DerivativesOverlayAvailability.STALE,
            recommendation=DerivativesOverlayRecommendation.UNKNOWN,
        ),
        overlay(funding_rate=None),
    ):
        assert candidate._raw_derivatives_entry_block_reason(value) == (
            "NEW_ENTRY_BLOCKED_BY_DERIVATIVES_DATA_UNAVAILABLE"
        )
    assert service("momentum")._raw_derivatives_entry_block_reason(None) is None
