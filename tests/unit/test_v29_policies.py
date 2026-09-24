from dataclasses import replace

from investment.crypto.application.dynamic_paper_rebalance import (
    V29_STRATEGY_VERSIONS,
    dynamic_policy_for_version,
)
from investment.crypto.strategy_registry import StrategyRegistry


def test_v29_control_is_exact_v28_control_and_configs_roundtrip():
    baseline = dynamic_policy_for_version("dynamic-intraday-v2.8-production-control")
    control = dynamic_policy_for_version("dynamic-intraday-v2.9-production-control")

    assert replace(control, strategy_version=baseline.strategy_version) == baseline
    assert V29_STRATEGY_VERSIONS[0] == control.strategy_version
    for version in V29_STRATEGY_VERSIONS:
        assert StrategyRegistry().load(version) == dynamic_policy_for_version(version)


def test_v29_challengers_each_change_only_the_declared_hypothesis():
    control = dynamic_policy_for_version("dynamic-intraday-v2.9-production-control")
    expected_changes = {
        "dynamic-intraday-v2.9-volatility-relaxed": {
            "maximum_entry_volatility",
            "maximum_entry_volatility_quantile",
        },
        "dynamic-intraday-v2.9-pullback-window": {
            "minimum_entry_momentum_1h",
            "maximum_entry_momentum_1h",
            "maximum_entry_momentum_4h",
        },
        "dynamic-intraday-v2.9-concentration-relaxed": {
            "maximum_selection_concentration",
        },
    }

    for version, expected in expected_changes.items():
        challenger = dynamic_policy_for_version(version)
        changed = {
            field
            for field in control.__dataclass_fields__
            if field != "strategy_version"
            and getattr(control, field) != getattr(challenger, field)
        }
        assert changed == expected
