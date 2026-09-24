from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from investment.crypto.application.dynamic_paper_rebalance import dynamic_policy_for_version
from investment.crypto.strategy_registry import (
    StrategyConfigError,
    StrategyRegistry,
    load_strategy_policy,
    write_policy,
)

CONFIG_ROOT = Path(__file__).parents[2] / "config" / "strategies"


@pytest.mark.parametrize(
    "version",
    [
        "dynamic-intraday-v2.2",
        "dynamic-intraday-v2.3",
        "dynamic-intraday-v2.4",
        "dynamic-intraday-v2.5",
        "dynamic-intraday-v2.6",
        "dynamic-intraday-v2.7",
        "dynamic-intraday-v2.7-accuracy-v1",
        "dynamic-intraday-v2.8-long-short",
        "dynamic-intraday-v2.8-momentum",
        "dynamic-intraday-v2.8-production-control",
        "dynamic-intraday-v2.8-soft-penalty",
    ],
)
def test_bundled_strategy_snapshot_matches_published_policy(version: str) -> None:
    policy = StrategyRegistry(CONFIG_ROOT).load(version)

    assert policy == dynamic_policy_for_version(version)
    assert isinstance(policy.invested_fraction, Decimal)
    assert isinstance(policy.reentry_cooldown, timedelta)
    assert isinstance(policy.maximum_holding_period, timedelta)
    assert isinstance(policy.excluded_base_assets, tuple)


def test_registry_lists_versioned_snapshots() -> None:
    assert StrategyRegistry(CONFIG_ROOT).versions() == (
        "dynamic-intraday-v2.2",
        "dynamic-intraday-v2.3",
        "dynamic-intraday-v2.4",
        "dynamic-intraday-v2.5",
        "dynamic-intraday-v2.6",
        "dynamic-intraday-v2.7",
        "dynamic-intraday-v2.7-accuracy-v1",
        "dynamic-intraday-v2.8-long-short",
        "dynamic-intraday-v2.8-momentum",
        "dynamic-intraday-v2.8-production-control",
        "dynamic-intraday-v2.8-soft-penalty",
        "dynamic-intraday-v2.9-concentration-relaxed",
        "dynamic-intraday-v2.9-production-control",
        "dynamic-intraday-v2.9-pullback-window",
        "dynamic-intraday-v2.9-volatility-relaxed",
    )


def test_v24_uses_complete_schema_two_without_rewriting_legacy_snapshots() -> None:
    v23_source = CONFIG_ROOT / "dynamic-intraday-v2.3.toml"
    v24_source = CONFIG_ROOT / "dynamic-intraday-v2.4.toml"

    assert "schema_version = 1" in v23_source.read_text(encoding="utf-8")
    assert "schema_version = 2" in v24_source.read_text(encoding="utf-8")
    v24 = StrategyRegistry(CONFIG_ROOT).load("dynamic-intraday-v2.4")
    assert v24.extreme_score_penalty_threshold == 0.80
    assert v24.expected_return_calibration_mode == "SCORE_LINEAR"
    assert v24.selection_concentration_lookback == timedelta(hours=24)
    assert v24.maximum_rolling_asset_realized_loss_fraction == Decimal("0.005")
    assert v24.derivatives_overlay_mode == "SHADOW"


def test_v25_isolates_the_4h_entry_floor_and_proxy_basis_hypotheses() -> None:
    v24 = StrategyRegistry(CONFIG_ROOT).load("dynamic-intraday-v2.4")
    v25 = StrategyRegistry(CONFIG_ROOT).load("dynamic-intraday-v2.5")

    assert v25.minimum_entry_momentum_4h == 0.0
    assert v24.minimum_entry_momentum_4h == -0.03
    assert v25.derivatives_feature_version == "btc-squeeze-v3-mark-index-basis-proxy"
    assert v25.derivatives_overlay_mode == "SHADOW"
    assert v25.expected_return_4h_intercept == v24.expected_return_4h_intercept
    assert v25.expected_return_4h_score_slope == v24.expected_return_4h_score_slope


def test_v26_uses_schema_three_and_a_frozen_derivatives_entry_gate() -> None:
    source = CONFIG_ROOT / "dynamic-intraday-v2.6.toml"
    policy = StrategyRegistry(CONFIG_ROOT).load("dynamic-intraday-v2.6")

    assert "schema_version = 3" in source.read_text(encoding="utf-8")
    assert policy == dynamic_policy_for_version("dynamic-intraday-v2.6")
    assert policy.derivatives_overlay_mode == "ENTRY_GATE"
    assert policy.derivatives_minimum_funding_rate == Decimal("0.00005")
    assert policy.derivatives_minimum_basis_input_rate == Decimal("-0.0005")
    assert policy.derivatives_minimum_global_long_short_ratio == Decimal("1.0")
    assert policy.maximum_entry_volatility == 0.003
    assert policy.maximum_entry_momentum_4h == 0.005


def test_v27_uses_schema_four_and_separate_crowding_entry_gate() -> None:
    source = CONFIG_ROOT / "dynamic-intraday-v2.7.toml"
    policy = StrategyRegistry(CONFIG_ROOT).load("dynamic-intraday-v2.7")

    assert "schema_version = 4" in source.read_text(encoding="utf-8")
    assert policy == dynamic_policy_for_version("dynamic-intraday-v2.7")
    assert policy.minimum_entry_momentum_1h == -0.007
    assert policy.reentry_cooldown == timedelta(hours=4)
    assert policy.derivatives_feature_version == "btc-squeeze-v3-mark-index-basis-proxy"
    assert policy.crowding_feature_version == "btc-crowding-v1"
    assert policy.crowding_overlay_mode == "ENTRY_GATE"
    assert policy.crowding_maximum_long_score == 0.70
    assert policy.crowding_maximum_bearish_unwind_score == 0.55


def test_v27_accuracy_patch_uses_schema_five_and_tightens_only_entry_freshness() -> None:
    source = CONFIG_ROOT / "dynamic-intraday-v2.7-accuracy-v1.toml"
    frozen = StrategyRegistry(CONFIG_ROOT).load("dynamic-intraday-v2.7")
    policy = StrategyRegistry(CONFIG_ROOT).load("dynamic-intraday-v2.7-accuracy-v1")

    assert "schema_version = 5" in source.read_text(encoding="utf-8")
    assert policy == dynamic_policy_for_version("dynamic-intraday-v2.7-accuracy-v1")
    assert policy.maximum_entry_market_data_age == timedelta(minutes=20)
    assert policy.maximum_market_data_age == timedelta(minutes=30)
    assert (
        replace(
            policy, strategy_version=frozen.strategy_version, maximum_entry_market_data_age=None
        )
        == frozen
    )


def test_schema_two_rejects_a_missing_v24_field(tmp_path: Path) -> None:
    source = CONFIG_ROOT / "dynamic-intraday-v2.4.toml"
    target = tmp_path / source.name
    target.write_text(
        source.read_text(encoding="utf-8").replace("extreme_score_maximum_penalty = 0.25\n", ""),
        encoding="utf-8",
    )

    with pytest.raises(
        StrategyConfigError, match="missing policy fields: extreme_score_maximum_penalty"
    ):
        load_strategy_policy(target, expected_version="dynamic-intraday-v2.4")


def test_schema_one_is_reserved_for_legacy_versions(tmp_path: Path) -> None:
    source = CONFIG_ROOT / "dynamic-intraday-v2.3.toml"
    target = tmp_path / "dynamic-intraday-v2.4.toml"
    target.write_text(
        source.read_text(encoding="utf-8").replace(
            'strategy_version = "dynamic-intraday-v2.3"',
            'strategy_version = "dynamic-intraday-v2.4"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(StrategyConfigError, match="schema_version 1 is reserved"):
        load_strategy_policy(target, expected_version="dynamic-intraday-v2.4")


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "minimum_history_bars = 672\n",
            "",
            "missing policy fields: minimum_history_bars",
        ),
        (
            "minimum_history_bars = 672\n",
            'minimum_history_bars = 672\nshell_command = "unsafe"\n',
            "unknown policy fields: shell_command",
        ),
        (
            'turnover_sell_weight = "0.50"',
            'turnover_sell_weight = "-0.01"',
            "turnover_sell_weight must be in",
        ),
        (
            'turnover_sell_weight = "0.50"',
            "turnover_sell_weight = 0.50",
            "turnover_sell_weight must be a quoted decimal string",
        ),
    ],
)
def test_loader_rejects_missing_unknown_unsafe_and_lossy_values(
    tmp_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    source = CONFIG_ROOT / "dynamic-intraday-v2.3.toml"
    target = tmp_path / source.name
    target.write_text(source.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

    with pytest.raises(StrategyConfigError, match=message):
        load_strategy_policy(target, expected_version="dynamic-intraday-v2.3")


def test_registry_verifies_requested_version_identity(tmp_path: Path) -> None:
    source = CONFIG_ROOT / "dynamic-intraday-v2.2.toml"
    target = tmp_path / "dynamic-intraday-v2.3.toml"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(StrategyConfigError, match="strategy_version identity mismatch"):
        StrategyRegistry(tmp_path).load("dynamic-intraday-v2.3")


def test_registry_rejects_path_traversal_as_a_version() -> None:
    with pytest.raises(StrategyConfigError, match="invalid strategy version"):
        StrategyRegistry(CONFIG_ROOT).load("../dynamic-intraday-v2.3")


def test_writer_round_trips_deterministically_and_never_overwrites(tmp_path: Path) -> None:
    policy = dynamic_policy_for_version("dynamic-intraday-v2.3")
    target = tmp_path / f"{policy.strategy_version}.toml"

    assert write_policy(target, policy) == target
    first_payload = target.read_text(encoding="utf-8")
    assert write_policy(target, policy) == target
    assert target.read_text(encoding="utf-8") == first_payload
    assert load_strategy_policy(target, expected_version=policy.strategy_version) == policy

    changed = replace(policy, turnover_sell_weight=Decimal("0.75"))
    with pytest.raises(StrategyConfigError, match="strategy snapshot already exists"):
        write_policy(target, changed)


def test_writer_requires_filename_to_match_policy_identity(tmp_path: Path) -> None:
    policy = dynamic_policy_for_version("dynamic-intraday-v2.3")

    with pytest.raises(StrategyConfigError, match="strategy filename identity mismatch"):
        write_policy(tmp_path / "dynamic-intraday-v2.4.toml", policy)


def test_writer_preserves_legacy_schema_and_uses_schema_two_for_v24(tmp_path: Path) -> None:
    v23 = dynamic_policy_for_version("dynamic-intraday-v2.3")
    v24 = dynamic_policy_for_version("dynamic-intraday-v2.4")

    legacy_path = write_policy(tmp_path / f"{v23.strategy_version}.toml", v23)
    current_path = write_policy(tmp_path / f"{v24.strategy_version}.toml", v24)

    assert "schema_version = 1" in legacy_path.read_text(encoding="utf-8")
    assert "schema_version = 2" in current_path.read_text(encoding="utf-8")
    assert load_strategy_policy(legacy_path) == v23
    assert load_strategy_policy(current_path) == v24


def test_published_legacy_version_cannot_enable_v24_extensions(tmp_path: Path) -> None:
    policy = replace(
        dynamic_policy_for_version("dynamic-intraday-v2.3"),
        derivatives_overlay_mode="SHADOW",
    )

    with pytest.raises(StrategyConfigError, match="cannot configure schema-2 fields"):
        write_policy(tmp_path / f"{policy.strategy_version}.toml", policy)
