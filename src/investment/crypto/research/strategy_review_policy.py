"""Version-specific, immutable review thresholds.

The review analyzer is deliberately generic: it reads only frozen evidence and
does not know which running lane supplied it.  This small adapter binds a
review to the immutable strategy version that produced the experiment without
changing the default V2.3/V2.4 promotion contract.
"""

from __future__ import annotations

from dataclasses import replace

from investment.crypto.research.strategy_review import StrategyReviewThresholds

V25_RULE_CONTROL_VARIANT_ID = "v2.4-rule-control"
V26_RULE_CONTROL_VARIANT_ID = "v2.5-rule-control"
V27_RULE_CONTROL_VARIANT_ID = "v2.6-rule-control"
V25_MARK_INDEX_PROXY_SOURCE = "MARK_INDEX_PROXY"


def thresholds_for_strategy_version(
    strategy_version: str,
    derivatives_feature_version: str,
    *,
    base: StrategyReviewThresholds | None = None,
) -> StrategyReviewThresholds:
    """Return deterministic review thresholds for one frozen strategy.

    V2.5 is a decision-only falsification experiment.  Its paper-readiness
    review additionally requires complete same-input V2.4-control evidence
    and a usable, consistently sourced V3 Mark--Index input.  The default
    threshold values intentionally leave all older versions unchanged.
    """

    thresholds = base or StrategyReviewThresholds()
    if strategy_version == "dynamic-intraday-v2.5":
        return replace(
            thresholds,
            required_derivatives_feature_version=derivatives_feature_version,
            minimum_derivatives_context_coverage=0.95,
            minimum_derivatives_available_rate=0.95,
            required_derivatives_basis_input_source=V25_MARK_INDEX_PROXY_SOURCE,
            minimum_derivatives_basis_input_source_match_rate=1.0,
            selection_control_variant_id=V25_RULE_CONTROL_VARIANT_ID,
            minimum_selection_control_variant_coverage=1.0,
            minimum_selection_control_paired_4h_cohorts=96,
            minimum_selection_control_changed_cohorts=30,
            minimum_selection_control_outcome_coverage=0.90,
            maximum_selection_control_outcome_coverage_gap=0.05,
            minimum_selection_control_actual_minus_control=0.0,
        )
    if strategy_version == "dynamic-intraday-v2.6":
        return replace(
            thresholds,
            required_derivatives_feature_version=derivatives_feature_version,
            minimum_derivatives_context_coverage=0.95,
            minimum_derivatives_available_rate=0.95,
            required_derivatives_basis_input_source=V25_MARK_INDEX_PROXY_SOURCE,
            minimum_derivatives_basis_input_source_match_rate=1.0,
            selection_control_variant_id=V26_RULE_CONTROL_VARIANT_ID,
            minimum_selection_control_variant_coverage=1.0,
            minimum_selection_control_paired_4h_cohorts=96,
            minimum_selection_control_changed_cohorts=30,
            minimum_selection_control_outcome_coverage=0.90,
            maximum_selection_control_outcome_coverage_gap=0.05,
            minimum_selection_control_actual_minus_control=0.0,
        )
    if strategy_version == "dynamic-intraday-v2.7":
        return replace(
            thresholds,
            required_derivatives_feature_version=derivatives_feature_version,
            minimum_derivatives_context_coverage=0.95,
            minimum_derivatives_available_rate=0.95,
            required_derivatives_basis_input_source=V25_MARK_INDEX_PROXY_SOURCE,
            minimum_derivatives_basis_input_source_match_rate=1.0,
            selection_control_variant_id=V27_RULE_CONTROL_VARIANT_ID,
            minimum_selection_control_variant_coverage=1.0,
            minimum_selection_control_paired_4h_cohorts=96,
            minimum_selection_control_changed_cohorts=30,
            minimum_selection_control_outcome_coverage=0.90,
            maximum_selection_control_outcome_coverage_gap=0.05,
            minimum_selection_control_actual_minus_control=0.0,
        )
    if strategy_version == "dynamic-intraday-v2.7-accuracy-v1":
        # This patch has its own A/B/C/D descriptive variants.  They are not a
        # promotion control, so no existing single-control gate is reused.
        return replace(
            thresholds,
            required_derivatives_feature_version=derivatives_feature_version,
            minimum_derivatives_context_coverage=0.95,
            minimum_derivatives_available_rate=0.95,
            required_derivatives_basis_input_source=V25_MARK_INDEX_PROXY_SOURCE,
            minimum_derivatives_basis_input_source_match_rate=1.0,
        )
    return replace(
        thresholds,
        required_derivatives_feature_version=derivatives_feature_version,
    )
