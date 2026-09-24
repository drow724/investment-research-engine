"""Strict TOML registry for immutable dynamic-universe strategy policies.

The runtime still decides which strategy is active.  This module only turns a
complete, versioned configuration snapshot into ``DynamicUniversePolicy``.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
import tomllib
from collections.abc import Collection
from dataclasses import fields
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

from investment.crypto.application.dynamic_paper_rebalance import DynamicUniversePolicy

STRATEGY_CONFIG_SCHEMA_VERSION = 5
DEFAULT_STRATEGY_CONFIG_ROOT = Path("config/strategies")
MAXIMUM_CONFIG_BYTES = 128 * 1024

_VERSION_PATTERN = re.compile(
    r"dynamic-intraday-v[1-9][0-9]*\.[0-9]+(?:-[a-z0-9]+(?:[.-][a-z0-9]+)*)?"
)
_ASSET_PATTERN = re.compile(r"[A-Z0-9]{2,20}")
_ROOT_FIELDS = frozenset({"schema_version", "strategy_version", "null_fields", "policy"})
_TURNOVER_BUDGET_MODES = frozenset({"FIXED", "BULLISH_REGIME"})
_SCORING_METHODS = frozenset({"RAW_MOMENTUM", "CALM_PULLBACK_RANK"})
_SCORE_SCALES = frozenset({"EXPECTED_RETURN", "UNIT_INTERVAL"})
_EXPECTED_RETURN_CALIBRATION_MODES = frozenset({"DISABLED", "SCORE_LINEAR"})
_DERIVATIVES_OVERLAY_MODES = frozenset({"DISABLED", "SHADOW", "ENTRY_GATE"})
_SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2, 3, 4, STRATEGY_CONFIG_SCHEMA_VERSION})
_LEGACY_STRATEGY_VERSIONS = frozenset(
    {
        "dynamic-intraday-v2.1",
        "dynamic-intraday-v2.2",
        "dynamic-intraday-v2.3",
    }
)
_SCHEMA_V2_STRATEGY_VERSIONS = frozenset({"dynamic-intraday-v2.4", "dynamic-intraday-v2.5"})
_SCHEMA_V3_STRATEGY_VERSIONS = frozenset({"dynamic-intraday-v2.6"})
_SCHEMA_V4_STRATEGY_VERSIONS = frozenset({"dynamic-intraday-v2.7"})
_SCHEMA_V1_POLICY_FIELDS = frozenset(
    {
        "minimum_history_bars",
        "liquidity_lookback_bars",
        "maximum_candidates",
        "maximum_positions",
        "invested_fraction",
        "maximum_asset_weight",
        "minimum_order_notional",
        "minimum_rebalance_fraction",
        "exchange_fee_rate",
        "estimated_slippage_rate",
        "entry_score_hurdle",
        "hold_score_hurdle",
        "exit_score_hurdle",
        "maximum_hold_rank",
        "required_entry_confirmations",
        "reentry_cooldown",
        "minimum_replacement_score_advantage",
        "maximum_daily_turnover_fraction",
        "turnover_sell_weight",
        "turnover_budget_mode",
        "bullish_daily_turnover_fraction",
        "bullish_btc_minimum_momentum_4h",
        "bullish_btc_minimum_momentum_24h",
        "bullish_market_breadth_minimum",
        "bullish_market_breadth_minimum_candidates",
        "maximum_daily_fee_fraction",
        "maximum_daily_realized_loss_fraction",
        "scoring_method",
        "score_scale",
        "momentum_1h_weight",
        "momentum_4h_weight",
        "momentum_24h_weight",
        "volatility_weight",
        "minimum_entry_momentum_1h",
        "maximum_entry_momentum_1h",
        "minimum_entry_momentum_4h",
        "maximum_entry_momentum_4h",
        "minimum_entry_momentum_24h",
        "maximum_entry_momentum_24h",
        "maximum_entry_volatility",
        "maximum_entry_volatility_quantile",
        "minimum_hold_momentum_1h",
        "minimum_hold_momentum_24h",
        "maximum_hold_volatility",
        "maximum_holding_period",
        "excluded_base_assets",
    }
)
_SCHEMA_V2_POLICY_FIELDS = _SCHEMA_V1_POLICY_FIELDS | frozenset(
    {
        "maximum_market_data_age",
        "extreme_score_penalty_threshold",
        "extreme_score_maximum_penalty",
        "expected_return_calibration_mode",
        "expected_return_1h_intercept",
        "expected_return_1h_score_slope",
        "expected_return_4h_intercept",
        "expected_return_4h_score_slope",
        "minimum_fee_adjusted_entry_return",
        "minimum_expected_hold_return",
        "minimum_fee_adjusted_replacement_advantage",
        "selection_concentration_lookback",
        "maximum_selection_concentration",
        "selection_concentration_minimum_cohorts",
        "rolling_asset_performance_lookback",
        "rolling_asset_minimum_sells",
        "maximum_rolling_asset_realized_loss_fraction",
        "derivatives_overlay_mode",
        "derivatives_feature_version",
        "derivatives_maximum_age",
    }
)
_SCHEMA_V3_POLICY_FIELDS = _SCHEMA_V2_POLICY_FIELDS | frozenset(
    {
        "derivatives_minimum_funding_rate",
        "derivatives_minimum_basis_input_rate",
        "derivatives_minimum_global_long_short_ratio",
    }
)
_SCHEMA_V4_POLICY_FIELDS = _SCHEMA_V3_POLICY_FIELDS | frozenset(
    {
        "crowding_overlay_mode",
        "crowding_feature_version",
        "crowding_maximum_age",
        "crowding_maximum_long_score",
        "crowding_maximum_bearish_unwind_score",
    }
)


class StrategyConfigError(ValueError):
    """A strategy configuration is malformed, incomplete, or unsafe to use."""


class StrategyRegistry:
    """Read-only registry whose file names are the published strategy versions."""

    def __init__(self, root: str | Path = DEFAULT_STRATEGY_CONFIG_ROOT) -> None:
        self.root = Path(root)

    def versions(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        if not self.root.is_dir():
            raise StrategyConfigError(f"strategy registry is not a directory: {self.root}")
        versions: list[str] = []
        for path in self.root.iterdir():
            if path.suffix != ".toml":
                continue
            if path.is_symlink() or not path.is_file():
                raise StrategyConfigError(f"strategy configuration must be a regular file: {path}")
            version = path.stem
            _validate_version(version)
            versions.append(version)
        return tuple(sorted(versions))

    def load(self, strategy_version: str) -> DynamicUniversePolicy:
        _validate_version(strategy_version)
        root = self.root.resolve()
        candidate = root / f"{strategy_version}.toml"
        if candidate.is_symlink():
            raise StrategyConfigError("symbolic-link strategy configurations are not allowed")
        path = candidate.resolve()
        if path.parent != root:
            raise StrategyConfigError("strategy configuration must stay inside registry root")
        if not path.exists():
            raise StrategyConfigError(f"strategy configuration not found: {strategy_version}")
        return load_strategy_policy(path, expected_version=strategy_version)


def load_strategy_policy(
    path: str | Path,
    *,
    expected_version: str | None = None,
) -> DynamicUniversePolicy:
    """Load and validate one complete policy snapshot.

    Decimal fields must be quoted decimal strings, durations are integer
    seconds, and tuple fields are TOML arrays.  TOML has no null literal, so
    optional values that are intentionally unset must be named in
    ``null_fields``.
    """

    source = Path(path)
    if source.suffix != ".toml":
        raise StrategyConfigError("strategy configuration must use the .toml suffix")
    if source.is_symlink():
        raise StrategyConfigError("symbolic-link strategy configurations are not allowed")
    if not source.is_file():
        raise StrategyConfigError(f"strategy configuration not found: {source}")
    payload = source.read_bytes()
    if len(payload) > MAXIMUM_CONFIG_BYTES:
        raise StrategyConfigError("strategy configuration exceeds the safe size limit")
    try:
        document = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise StrategyConfigError(f"invalid TOML strategy configuration: {exc}") from exc
    filename_version = source.stem
    _validate_version(filename_version)
    if expected_version is not None and expected_version != filename_version:
        raise StrategyConfigError(
            "strategy filename identity mismatch: "
            f"expected {expected_version}.toml, found {source.name}"
        )
    return _policy_from_document(document, expected_version=filename_version)


def serialize_strategy_policy(policy: DynamicUniversePolicy) -> str:
    """Return the canonical TOML representation of a validated policy."""

    _validate_version(policy.strategy_version)
    _validate_policy(policy)
    schema_version = _schema_version_for_policy(policy)
    policy_fields = _policy_fields_for_schema(schema_version)
    null_fields: list[str] = []
    configured: list[tuple[str, Any]] = []
    for field in fields(policy):
        if field.name == "strategy_version" or field.name not in policy_fields:
            continue
        value = getattr(policy, field.name)
        if value is None:
            null_fields.append(field.name)
        else:
            configured.append((field.name, value))
    lines = [
        "# Generated immutable strategy snapshot. Do not edit after publication.",
        f"schema_version = {schema_version}",
        f"strategy_version = {_toml_string(policy.strategy_version)}",
        f"null_fields = {_toml_string_array(null_fields)}",
        "",
        "[policy]",
    ]
    lines.extend(f"{name} = {_encode_value(name, value)}" for name, value in configured)
    return "\n".join(lines) + "\n"


def strategy_policy_config_values(policy: DynamicUniversePolicy) -> dict[str, Any]:
    """Return the canonical policy mapping used by the frozen config hash.

    Published schema-1 policies intentionally omit fields introduced for V2.4.
    Keeping that projection stable preserves the hashes already stored with
    running and completed V2.2/V2.3 observation experiments.
    """

    _validate_version(policy.strategy_version)
    _validate_policy(policy)
    schema_version = _schema_version_for_policy(policy)
    policy_fields = _policy_fields_for_schema(schema_version)
    return {
        field.name: getattr(policy, field.name)
        for field in fields(policy)
        if field.name == "strategy_version" or field.name in policy_fields
    }


def write_policy(path: str | Path, policy: DynamicUniversePolicy) -> Path:
    """Publish a policy atomically without overwriting a different snapshot.

    Calling this repeatedly with the same policy is idempotent. A filename is
    part of the strategy identity and must therefore match ``strategy_version``.
    """

    destination = Path(path)
    if destination.suffix != ".toml":
        raise StrategyConfigError("strategy configuration must use the .toml suffix")
    if destination.stem != policy.strategy_version:
        raise StrategyConfigError(
            "strategy filename identity mismatch: "
            f"expected {policy.strategy_version}.toml, found {destination.name}"
        )
    if destination.is_symlink():
        raise StrategyConfigError("symbolic-link strategy configurations are not allowed")
    payload = serialize_strategy_policy(policy)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_file() and destination.read_text(encoding="utf-8") == payload:
            return destination
        raise StrategyConfigError(f"strategy snapshot already exists: {destination}")

    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.is_file() and destination.read_text(encoding="utf-8") == payload:
                return destination
            raise StrategyConfigError(f"strategy snapshot already exists: {destination}") from None
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _policy_from_document(
    document: dict[str, Any],
    *,
    expected_version: str | None,
) -> DynamicUniversePolicy:
    unknown_root = set(document) - _ROOT_FIELDS
    missing_root = _ROOT_FIELDS - set(document)
    if unknown_root:
        raise StrategyConfigError(f"unknown top-level fields: {_formatted(unknown_root)}")
    if missing_root:
        raise StrategyConfigError(f"missing top-level fields: {_formatted(missing_root)}")

    schema_version = document["schema_version"]
    if type(schema_version) is not int or schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        supported = ", ".join(str(value) for value in sorted(_SUPPORTED_SCHEMA_VERSIONS))
        raise StrategyConfigError(f"schema_version must be one of: {supported}")
    strategy_version = document["strategy_version"]
    if not isinstance(strategy_version, str):
        raise StrategyConfigError("strategy_version must be a string")
    _validate_version(strategy_version)
    if expected_version is not None:
        _validate_version(expected_version)
        if strategy_version != expected_version:
            raise StrategyConfigError(
                "strategy_version identity mismatch: "
                f"expected {expected_version}, found {strategy_version}"
            )
    if schema_version == 1 and strategy_version not in _LEGACY_STRATEGY_VERSIONS:
        raise StrategyConfigError("schema_version 1 is reserved for published legacy strategies")
    if schema_version == 2 and strategy_version not in _SCHEMA_V2_STRATEGY_VERSIONS:
        raise StrategyConfigError("schema_version 2 is reserved for published V2.4/V2.5 strategies")
    if schema_version == 3 and strategy_version not in _SCHEMA_V3_STRATEGY_VERSIONS:
        raise StrategyConfigError("schema_version 3 is reserved for published V2.6 strategy")
    if schema_version == 4 and strategy_version not in _SCHEMA_V4_STRATEGY_VERSIONS:
        raise StrategyConfigError("schema_version 4 is reserved for published V2.7 strategy")
    if schema_version == STRATEGY_CONFIG_SCHEMA_VERSION and strategy_version in (
        _LEGACY_STRATEGY_VERSIONS
        | _SCHEMA_V2_STRATEGY_VERSIONS
        | _SCHEMA_V3_STRATEGY_VERSIONS
        | _SCHEMA_V4_STRATEGY_VERSIONS
    ):
        raise StrategyConfigError("published older strategies must retain their original schema")

    null_fields_value = document["null_fields"]
    if not isinstance(null_fields_value, list) or not all(
        isinstance(item, str) for item in null_fields_value
    ):
        raise StrategyConfigError("null_fields must be an array of field-name strings")
    if len(set(null_fields_value)) != len(null_fields_value):
        raise StrategyConfigError("null_fields must not contain duplicates")
    null_fields = set(null_fields_value)

    policy_values = document["policy"]
    if not isinstance(policy_values, dict):
        raise StrategyConfigError("policy must be a TOML table")

    annotations = get_type_hints(DynamicUniversePolicy)
    all_fields = {field.name for field in fields(DynamicUniversePolicy)} - {"strategy_version"}
    expected_fields = _policy_fields_for_schema(schema_version)
    supplied_fields = set(policy_values)
    overlap = supplied_fields & null_fields
    if overlap:
        raise StrategyConfigError(
            f"fields cannot be both configured and null: {_formatted(overlap)}"
        )
    unknown_fields = (supplied_fields | null_fields) - expected_fields
    if unknown_fields:
        raise StrategyConfigError(f"unknown policy fields: {_formatted(unknown_fields)}")
    missing_fields = expected_fields - supplied_fields - null_fields
    if missing_fields:
        raise StrategyConfigError(f"missing policy fields: {_formatted(missing_fields)}")

    optional_fields = {
        name for name in expected_fields if type(None) in get_args(annotations[name])
    }
    invalid_nulls = null_fields - optional_fields
    if invalid_nulls:
        raise StrategyConfigError(
            f"non-optional fields cannot be null: {_formatted(invalid_nulls)}"
        )

    defaults = DynamicUniversePolicy()
    values: dict[str, Any] = {
        field.name: getattr(defaults, field.name)
        for field in fields(defaults)
        if field.name in all_fields
    }
    values["strategy_version"] = strategy_version
    values.update(dict.fromkeys(null_fields))
    for name, raw_value in policy_values.items():
        values[name] = _decode_value(name, raw_value, annotations[name])
    policy = DynamicUniversePolicy(**values)
    _validate_policy(policy)
    return policy


def _decode_value(name: str, raw_value: Any, annotation: Any) -> Any:
    arguments = get_args(annotation)
    if type(None) in arguments:
        non_null = tuple(argument for argument in arguments if argument is not type(None))
        if len(non_null) != 1:
            raise StrategyConfigError(f"unsupported optional type for {name}")
        annotation = non_null[0]

    if annotation is Decimal:
        if not isinstance(raw_value, str):
            raise StrategyConfigError(f"{name} must be a quoted decimal string")
        try:
            value = Decimal(raw_value)
        except InvalidOperation as exc:
            raise StrategyConfigError(f"{name} is not a valid decimal") from exc
        if not value.is_finite():
            raise StrategyConfigError(f"{name} must be finite")
        return value
    if annotation is timedelta:
        if type(raw_value) is not int:
            raise StrategyConfigError(f"{name} must be an integer number of seconds")
        return timedelta(seconds=raw_value)
    if get_origin(annotation) is tuple:
        if not isinstance(raw_value, list) or not all(isinstance(item, str) for item in raw_value):
            raise StrategyConfigError(f"{name} must be an array of strings")
        return tuple(raw_value)
    if annotation is int:
        if type(raw_value) is not int:
            raise StrategyConfigError(f"{name} must be an integer")
        return raw_value
    if annotation is float:
        if type(raw_value) is not float or not math.isfinite(raw_value):
            raise StrategyConfigError(f"{name} must be a finite TOML float")
        return raw_value
    if annotation is str:
        if not isinstance(raw_value, str):
            raise StrategyConfigError(f"{name} must be a string")
        return raw_value
    raise StrategyConfigError(f"unsupported policy type for {name}")


def _encode_value(name: str, value: Any) -> str:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise StrategyConfigError(f"{name} must be finite")
        return _toml_string(str(value))
    if isinstance(value, timedelta):
        if value.microseconds:
            raise StrategyConfigError(f"{name} must use whole-second precision")
        seconds = value.days * 86_400 + value.seconds
        return str(seconds)
    if isinstance(value, tuple):
        if not all(isinstance(item, str) for item in value):
            raise StrategyConfigError(f"{name} must contain only strings")
        return _toml_string_array(list(value))
    if type(value) is int:
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise StrategyConfigError(f"{name} must be finite")
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    raise StrategyConfigError(f"unsupported policy value for {name}")


def _validate_policy(policy: DynamicUniversePolicy) -> None:
    _validate_runtime_policy_types(policy)
    positive_integers = {
        "minimum_history_bars": policy.minimum_history_bars,
        "liquidity_lookback_bars": policy.liquidity_lookback_bars,
        "maximum_candidates": policy.maximum_candidates,
        "maximum_positions": policy.maximum_positions,
        "maximum_hold_rank": policy.maximum_hold_rank,
        "required_entry_confirmations": policy.required_entry_confirmations,
        "bullish_market_breadth_minimum_candidates": (
            policy.bullish_market_breadth_minimum_candidates
        ),
        "selection_concentration_minimum_cohorts": (policy.selection_concentration_minimum_cohorts),
        "rolling_asset_minimum_sells": policy.rolling_asset_minimum_sells,
    }
    for integer_name, integer_value in positive_integers.items():
        if integer_value <= 0:
            raise StrategyConfigError(f"{integer_name} must be greater than zero")
    if policy.liquidity_lookback_bars > policy.minimum_history_bars:
        raise StrategyConfigError("liquidity lookback cannot exceed minimum history")
    if policy.maximum_positions > policy.maximum_candidates:
        raise StrategyConfigError("maximum_positions cannot exceed maximum_candidates")
    if policy.maximum_hold_rank > policy.maximum_candidates:
        raise StrategyConfigError("maximum_hold_rank cannot exceed maximum_candidates")
    if policy.bullish_market_breadth_minimum_candidates > policy.maximum_candidates:
        raise StrategyConfigError(
            "bullish breadth minimum candidates cannot exceed maximum_candidates"
        )

    _require_decimal_between(
        "invested_fraction",
        policy.invested_fraction,
        Decimal("0"),
        Decimal("1"),
        lower_open=True,
    )
    _require_decimal_between(
        "maximum_asset_weight",
        policy.maximum_asset_weight,
        Decimal("0"),
        Decimal("1"),
        lower_open=True,
    )
    if policy.maximum_asset_weight * policy.maximum_positions < policy.invested_fraction:
        raise StrategyConfigError("position limits cannot reach invested_fraction")
    if policy.minimum_order_notional <= 0:
        raise StrategyConfigError("minimum_order_notional must be greater than zero")
    _require_decimal_between(
        "minimum_rebalance_fraction",
        policy.minimum_rebalance_fraction,
        Decimal("0"),
        Decimal("1"),
    )
    for decimal_name, decimal_value in (
        ("exchange_fee_rate", policy.exchange_fee_rate),
        ("estimated_slippage_rate", policy.estimated_slippage_rate),
        ("maximum_daily_fee_fraction", policy.maximum_daily_fee_fraction),
        ("maximum_daily_realized_loss_fraction", policy.maximum_daily_realized_loss_fraction),
    ):
        _require_decimal_between(
            decimal_name,
            decimal_value,
            Decimal("0"),
            Decimal("1"),
            upper_open=True,
        )
    if policy.maximum_daily_turnover_fraction <= 0:
        raise StrategyConfigError("maximum_daily_turnover_fraction must be greater than zero")
    _require_decimal_between(
        "turnover_sell_weight", policy.turnover_sell_weight, Decimal("0"), Decimal("1")
    )

    if policy.turnover_budget_mode not in _TURNOVER_BUDGET_MODES:
        raise StrategyConfigError("unsupported turnover_budget_mode")
    if policy.turnover_budget_mode == "FIXED":
        if policy.bullish_daily_turnover_fraction is not None:
            raise StrategyConfigError("FIXED turnover mode cannot define a bullish turnover limit")
    elif policy.bullish_daily_turnover_fraction is None:
        raise StrategyConfigError("BULLISH_REGIME mode requires a bullish turnover limit")
    elif policy.bullish_daily_turnover_fraction < policy.maximum_daily_turnover_fraction:
        raise StrategyConfigError("bullish turnover limit cannot be below the base limit")

    _require_float_between(
        "bullish_market_breadth_minimum",
        policy.bullish_market_breadth_minimum,
        0.0,
        1.0,
    )
    if policy.scoring_method not in _SCORING_METHODS:
        raise StrategyConfigError("unsupported scoring_method")
    if policy.score_scale not in _SCORE_SCALES:
        raise StrategyConfigError("unsupported score_scale")
    if policy.expected_return_calibration_mode not in _EXPECTED_RETURN_CALIBRATION_MODES:
        raise StrategyConfigError("unsupported expected_return_calibration_mode")
    if policy.derivatives_overlay_mode not in _DERIVATIVES_OVERLAY_MODES | {
        "LONG_SHORT_GATE",
        "SOFT_PENALTY",
    }:
        raise StrategyConfigError("unsupported derivatives_overlay_mode")
    if policy.crowding_overlay_mode not in _DERIVATIVES_OVERLAY_MODES:
        raise StrategyConfigError("unsupported crowding_overlay_mode")
    if not policy.derivatives_feature_version.strip():
        raise StrategyConfigError("derivatives_feature_version must not be blank")
    derivative_gate_values = (
        policy.derivatives_minimum_funding_rate,
        policy.derivatives_minimum_basis_input_rate,
        policy.derivatives_minimum_global_long_short_ratio,
    )
    if policy.derivatives_overlay_mode in {"ENTRY_GATE", "LONG_SHORT_GATE", "SOFT_PENALTY"}:
        if any(value is None for value in derivative_gate_values):
            raise StrategyConfigError("ENTRY_GATE requires every derivatives regime threshold")
        if policy.derivatives_minimum_global_long_short_ratio is not None and (
            policy.derivatives_minimum_global_long_short_ratio <= 0
        ):
            raise StrategyConfigError(
                "derivatives_minimum_global_long_short_ratio must be positive"
            )
    elif any(value is not None for value in derivative_gate_values):
        raise StrategyConfigError(
            "derivatives regime thresholds require derivatives_overlay_mode ENTRY_GATE"
        )
    crowding_gate_values = (
        policy.crowding_maximum_long_score,
        policy.crowding_maximum_bearish_unwind_score,
    )
    if not policy.crowding_feature_version.strip():
        raise StrategyConfigError("crowding_feature_version must not be blank")
    if policy.crowding_overlay_mode == "ENTRY_GATE":
        if any(value is None for value in crowding_gate_values):
            raise StrategyConfigError("crowding ENTRY_GATE requires both risk thresholds")
        for name, value in (
            ("crowding_maximum_long_score", policy.crowding_maximum_long_score),
            (
                "crowding_maximum_bearish_unwind_score",
                policy.crowding_maximum_bearish_unwind_score,
            ),
        ):
            assert value is not None
            _require_float_between(name, value, 0.0, 1.0)
    elif any(value is not None for value in crowding_gate_values):
        raise StrategyConfigError("crowding thresholds require crowding ENTRY_GATE")

    float_values = {
        field.name: getattr(policy, field.name)
        for field in fields(policy)
        if isinstance(getattr(policy, field.name), float)
    }
    for name, value in float_values.items():
        if not math.isfinite(value):
            raise StrategyConfigError(f"{name} must be finite")
    if policy.score_scale == "UNIT_INTERVAL":
        for score_name, score_value in (
            ("entry_score_hurdle", policy.entry_score_hurdle),
            ("hold_score_hurdle", policy.hold_score_hurdle),
            ("exit_score_hurdle", policy.exit_score_hurdle),
            (
                "minimum_replacement_score_advantage",
                policy.minimum_replacement_score_advantage,
            ),
        ):
            _require_float_between(score_name, score_value, 0.0, 1.0)

    threshold = policy.extreme_score_penalty_threshold
    maximum_penalty = policy.extreme_score_maximum_penalty
    if threshold is None:
        if maximum_penalty != 0:
            raise StrategyConfigError(
                "extreme_score_maximum_penalty requires an extreme score threshold"
            )
    else:
        if not 0 <= threshold < 1:
            raise StrategyConfigError("extreme_score_penalty_threshold must be in [0, 1)")
        if not 0 < maximum_penalty <= 1:
            raise StrategyConfigError(
                "extreme_score_maximum_penalty must be in (0, 1] when enabled"
            )

    if policy.expected_return_calibration_mode == "DISABLED":
        calibration_values = (
            policy.expected_return_1h_intercept,
            policy.expected_return_1h_score_slope,
            policy.expected_return_4h_intercept,
            policy.expected_return_4h_score_slope,
        )
        if any(value != 0 for value in calibration_values):
            raise StrategyConfigError(
                "disabled expected-return calibration requires zero coefficients"
            )
    elif policy.expected_return_1h_score_slope <= 0 or policy.expected_return_4h_score_slope <= 0:
        raise StrategyConfigError("SCORE_LINEAR expected-return slopes must be positive")
    for name, value in (
        ("minimum_fee_adjusted_entry_return", policy.minimum_fee_adjusted_entry_return),
        ("minimum_expected_hold_return", policy.minimum_expected_hold_return),
        (
            "minimum_fee_adjusted_replacement_advantage",
            policy.minimum_fee_adjusted_replacement_advantage,
        ),
    ):
        if value < 0:
            raise StrategyConfigError(f"{name} cannot be negative")

    concentration_enabled = policy.selection_concentration_lookback is not None
    if concentration_enabled != (policy.maximum_selection_concentration is not None):
        raise StrategyConfigError(
            "selection concentration lookback and maximum must be configured together"
        )
    if policy.maximum_selection_concentration is not None and not (
        0 < policy.maximum_selection_concentration <= 1
    ):
        raise StrategyConfigError("maximum_selection_concentration must be in (0, 1]")

    rolling_loss_enabled = policy.rolling_asset_performance_lookback is not None
    if rolling_loss_enabled != (policy.maximum_rolling_asset_realized_loss_fraction is not None):
        raise StrategyConfigError(
            "rolling asset lookback and realized-loss fraction must be configured together"
        )
    if policy.maximum_rolling_asset_realized_loss_fraction is not None:
        _require_decimal_between(
            "maximum_rolling_asset_realized_loss_fraction",
            policy.maximum_rolling_asset_realized_loss_fraction,
            Decimal("0"),
            Decimal("1"),
            lower_open=True,
            upper_open=True,
        )

    for volatility_name, volatility_value in (
        ("maximum_entry_volatility", policy.maximum_entry_volatility),
        ("maximum_hold_volatility", policy.maximum_hold_volatility),
    ):
        if volatility_value is not None and volatility_value < 0:
            raise StrategyConfigError(f"{volatility_name} cannot be negative")
    if policy.maximum_entry_volatility_quantile is not None:
        _require_float_between(
            "maximum_entry_volatility_quantile",
            policy.maximum_entry_volatility_quantile,
            0.0,
            1.0,
        )
    for minimum_name, maximum_name in (
        ("minimum_entry_momentum_1h", "maximum_entry_momentum_1h"),
        ("minimum_entry_momentum_4h", "maximum_entry_momentum_4h"),
        ("minimum_entry_momentum_24h", "maximum_entry_momentum_24h"),
    ):
        minimum = getattr(policy, minimum_name)
        maximum = getattr(policy, maximum_name)
        if minimum is not None and maximum is not None and minimum > maximum:
            raise StrategyConfigError(f"{minimum_name} cannot exceed {maximum_name}")

    if policy.reentry_cooldown <= timedelta(0):
        raise StrategyConfigError("reentry_cooldown must be greater than zero")
    if policy.maximum_holding_period is not None and policy.maximum_holding_period <= timedelta(0):
        raise StrategyConfigError("maximum_holding_period must be greater than zero")
    if policy.maximum_market_data_age is not None and policy.maximum_market_data_age <= timedelta(
        0
    ):
        raise StrategyConfigError("maximum_market_data_age must be greater than zero")
    if (
        policy.maximum_entry_market_data_age is not None
        and policy.maximum_entry_market_data_age <= timedelta(0)
    ):
        raise StrategyConfigError("maximum_entry_market_data_age must be greater than zero")
    if (
        policy.selection_concentration_lookback is not None
        and policy.selection_concentration_lookback <= timedelta(0)
    ):
        raise StrategyConfigError("selection_concentration_lookback must be greater than zero")
    if (
        policy.rolling_asset_performance_lookback is not None
        and policy.rolling_asset_performance_lookback <= timedelta(0)
    ):
        raise StrategyConfigError("rolling_asset_performance_lookback must be greater than zero")
    if policy.derivatives_maximum_age <= timedelta(0):
        raise StrategyConfigError("derivatives_maximum_age must be greater than zero")
    if policy.crowding_maximum_age <= timedelta(0):
        raise StrategyConfigError("crowding_maximum_age must be greater than zero")
    if len(set(policy.excluded_base_assets)) != len(policy.excluded_base_assets):
        raise StrategyConfigError("excluded_base_assets must not contain duplicates")
    invalid_assets = [
        asset for asset in policy.excluded_base_assets if _ASSET_PATTERN.fullmatch(asset) is None
    ]
    if invalid_assets:
        raise StrategyConfigError(
            f"invalid excluded_base_assets: {', '.join(sorted(invalid_assets))}"
        )


def _policy_fields_for_schema(schema_version: int) -> frozenset[str]:
    if schema_version == 1:
        return _SCHEMA_V1_POLICY_FIELDS
    if schema_version == 2:
        return _SCHEMA_V2_POLICY_FIELDS
    if schema_version == 3:
        return _SCHEMA_V3_POLICY_FIELDS
    if schema_version == 4:
        return _SCHEMA_V4_POLICY_FIELDS
    if schema_version == STRATEGY_CONFIG_SCHEMA_VERSION:
        return frozenset(
            field.name
            for field in fields(DynamicUniversePolicy)
            if field.name != "strategy_version"
        )
    raise StrategyConfigError(f"unsupported strategy schema version: {schema_version}")


def _schema_version_for_policy(policy: DynamicUniversePolicy) -> int:
    if policy.strategy_version in _SCHEMA_V2_STRATEGY_VERSIONS:
        defaults = DynamicUniversePolicy()
        schema3_fields = _policy_fields_for_schema(STRATEGY_CONFIG_SCHEMA_VERSION).difference(
            _SCHEMA_V2_POLICY_FIELDS
        )
        changed = {
            name for name in schema3_fields if getattr(policy, name) != getattr(defaults, name)
        }
        if changed:
            raise StrategyConfigError(
                "published schema-2 strategy cannot configure schema-3 fields: "
                + _formatted(changed)
            )
        return 2
    if policy.strategy_version in _SCHEMA_V3_STRATEGY_VERSIONS:
        defaults = DynamicUniversePolicy()
        schema4_fields = _policy_fields_for_schema(STRATEGY_CONFIG_SCHEMA_VERSION).difference(
            _SCHEMA_V3_POLICY_FIELDS
        )
        changed = {
            name for name in schema4_fields if getattr(policy, name) != getattr(defaults, name)
        }
        if changed:
            raise StrategyConfigError(
                "published schema-3 strategy cannot configure schema-4 fields: "
                + _formatted(changed)
            )
        return 3
    if policy.strategy_version in _SCHEMA_V4_STRATEGY_VERSIONS:
        defaults = DynamicUniversePolicy()
        schema5_fields = _policy_fields_for_schema(STRATEGY_CONFIG_SCHEMA_VERSION).difference(
            _SCHEMA_V4_POLICY_FIELDS
        )
        changed = {
            name for name in schema5_fields if getattr(policy, name) != getattr(defaults, name)
        }
        if changed:
            raise StrategyConfigError(
                "published schema-4 strategy cannot configure schema-5 fields: "
                + _formatted(changed)
            )
        return 4
    if policy.strategy_version not in _LEGACY_STRATEGY_VERSIONS:
        return STRATEGY_CONFIG_SCHEMA_VERSION
    defaults = DynamicUniversePolicy()
    extension_fields = _policy_fields_for_schema(STRATEGY_CONFIG_SCHEMA_VERSION).difference(
        _SCHEMA_V1_POLICY_FIELDS
    )
    changed = {
        name for name in extension_fields if getattr(policy, name) != getattr(defaults, name)
    }
    if changed:
        raise StrategyConfigError(
            "published legacy strategy cannot configure schema-2 fields: " + _formatted(changed)
        )
    return 1


def _validate_version(strategy_version: str) -> None:
    if _VERSION_PATTERN.fullmatch(strategy_version) is None:
        raise StrategyConfigError(f"invalid strategy version: {strategy_version!r}")


def _validate_runtime_policy_types(policy: DynamicUniversePolicy) -> None:
    annotations = get_type_hints(DynamicUniversePolicy)
    for field in fields(policy):
        name = field.name
        value = getattr(policy, name)
        annotation = annotations[name]
        arguments = get_args(annotation)
        optional = type(None) in arguments
        if value is None:
            if optional:
                continue
            raise StrategyConfigError(f"{name} cannot be null")
        if optional:
            non_null = tuple(argument for argument in arguments if argument is not type(None))
            if len(non_null) != 1:
                raise StrategyConfigError(f"unsupported optional type for {name}")
            annotation = non_null[0]

        valid = False
        if annotation is Decimal:
            valid = isinstance(value, Decimal) and value.is_finite()
        elif annotation is timedelta:
            valid = isinstance(value, timedelta)
        elif get_origin(annotation) is tuple:
            valid = isinstance(value, tuple) and all(isinstance(item, str) for item in value)
        elif annotation is int:
            valid = type(value) is int
        elif annotation is float:
            valid = type(value) is float and math.isfinite(value)
        elif annotation is str:
            valid = isinstance(value, str)
        if not valid:
            raise StrategyConfigError(f"{name} has an invalid runtime type or value")


def _require_decimal_between(
    name: str,
    value: Decimal,
    minimum: Decimal,
    maximum: Decimal,
    *,
    lower_open: bool = False,
    upper_open: bool = False,
) -> None:
    lower_invalid = value <= minimum if lower_open else value < minimum
    upper_invalid = value >= maximum if upper_open else value > maximum
    if lower_invalid or upper_invalid:
        left = "(" if lower_open else "["
        right = ")" if upper_open else "]"
        raise StrategyConfigError(f"{name} must be in {left}{minimum}, {maximum}{right}")


def _require_float_between(name: str, value: float, minimum: float, maximum: float) -> None:
    if value < minimum or value > maximum:
        raise StrategyConfigError(f"{name} must be between {minimum} and {maximum}")


def _formatted(values: Collection[str]) -> str:
    return ", ".join(sorted(values))


def _toml_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\b", "\\b")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\f", "\\f")
        .replace("\r", "\\r")
    )
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in escaped):
        raise StrategyConfigError("strategy strings cannot contain control characters")
    return f'"{escaped}"'


def _toml_string_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"
