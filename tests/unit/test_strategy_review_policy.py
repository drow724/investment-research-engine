from dataclasses import replace

from investment.crypto.research.strategy_review import StrategyReviewThresholds
from investment.crypto.research.strategy_review_policy import (
    V25_MARK_INDEX_PROXY_SOURCE,
    V25_RULE_CONTROL_VARIANT_ID,
    V26_RULE_CONTROL_VARIANT_ID,
    thresholds_for_strategy_version,
)


def test_v25_thresholds_require_proxy_provenance_and_same_input_control() -> None:
    thresholds = thresholds_for_strategy_version(
        "dynamic-intraday-v2.5",
        "btc-squeeze-v3-mark-index-basis-proxy",
    )

    assert thresholds.required_derivatives_feature_version == (
        "btc-squeeze-v3-mark-index-basis-proxy"
    )
    assert thresholds.minimum_derivatives_context_coverage == 0.95
    assert thresholds.minimum_derivatives_available_rate == 0.95
    assert thresholds.required_derivatives_basis_input_source == V25_MARK_INDEX_PROXY_SOURCE
    assert thresholds.minimum_derivatives_basis_input_source_match_rate == 1.0
    assert thresholds.selection_control_variant_id == V25_RULE_CONTROL_VARIANT_ID
    assert thresholds.minimum_selection_control_variant_coverage == 1.0
    assert thresholds.minimum_selection_control_paired_4h_cohorts == 96
    assert thresholds.minimum_selection_control_changed_cohorts == 30
    assert thresholds.minimum_selection_control_outcome_coverage == 0.90
    assert thresholds.maximum_selection_control_outcome_coverage_gap == 0.05
    assert thresholds.minimum_selection_control_actual_minus_control == 0.0


def test_older_version_keeps_existing_thresholds_except_frozen_feature_identity() -> None:
    base = replace(StrategyReviewThresholds(), minimum_derivatives_available_rate=0.87)

    result = thresholds_for_strategy_version(
        "dynamic-intraday-v2.4",
        "btc-squeeze-v2-market-streams",
        base=base,
    )

    assert result == base


def test_v26_thresholds_require_v25_control_and_proxy_provenance() -> None:
    thresholds = thresholds_for_strategy_version(
        "dynamic-intraday-v2.6",
        "btc-squeeze-v3-mark-index-basis-proxy",
    )

    assert thresholds.selection_control_variant_id == V26_RULE_CONTROL_VARIANT_ID
    assert thresholds.required_derivatives_basis_input_source == V25_MARK_INDEX_PROXY_SOURCE
    assert thresholds.minimum_selection_control_paired_4h_cohorts == 96


def test_v27_accuracy_uses_proxy_quality_without_reusing_a_single_control_gate() -> None:
    thresholds = thresholds_for_strategy_version(
        "dynamic-intraday-v2.7-accuracy-v1",
        "btc-squeeze-v3-mark-index-basis-proxy",
    )

    assert thresholds.required_derivatives_basis_input_source == V25_MARK_INDEX_PROXY_SOURCE
    assert thresholds.selection_control_variant_id is None
    assert thresholds.economic_round_trip_cost == 0.002
