"""Versioned linear baselines over normalized features."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum

import polars as pl


class HistoryScope(StrEnum):
    LONG_HISTORY = "LONG_HISTORY"
    POST_ETF = "POST_ETF"


@dataclass(frozen=True, slots=True)
class CompositeFeatureMetadata:
    feature_name: str
    feature_version: str
    input_features: tuple[str, ...]
    weights: dict[str, float]
    parameters: dict[str, object]
    history_scope: HistoryScope
    created_at: datetime

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class WeightedCompositeFeature:
    def __init__(
        self,
        name: str,
        weights: dict[str, float],
        *,
        version: str = "v1",
        history_scope: HistoryScope = HistoryScope.LONG_HISTORY,
        created_at: datetime | None = None,
    ) -> None:
        if not weights:
            raise ValueError("a composite feature needs at least one input")
        self.name = name
        self.weights = dict(weights)
        self.metadata = CompositeFeatureMetadata(
            feature_name=name,
            feature_version=version,
            input_features=tuple(weights),
            weights=dict(weights),
            parameters={"missing_data": "NO_FILL"},
            history_scope=history_scope,
            created_at=created_at or datetime.now(UTC),
        )

    def compute(self, frame: pl.DataFrame) -> pl.DataFrame:
        missing = set(self.weights).difference(frame.columns)
        if missing:
            raise ValueError(f"missing composite inputs: {sorted(missing)}")
        expression: pl.Expr | None = None
        for feature, weight in self.weights.items():
            component = pl.col(feature) * weight
            expression = component if expression is None else expression + component
        if expression is None:
            raise AssertionError("weights were validated as non-empty")
        return frame.with_columns(expression.alias(self.name))


class DemandPressureFeature(WeightedCompositeFeature):
    def __init__(self, inputs: tuple[str, ...] = ("etf_flow_zscore_20d",)) -> None:
        super().__init__(
            "demand_pressure",
            dict.fromkeys(inputs, 1.0),
            history_scope=(
                HistoryScope.POST_ETF
                if "etf_flow_zscore_20d" in inputs
                else HistoryScope.LONG_HISTORY
            ),
        )


class SupplyPressureFeature(WeightedCompositeFeature):
    def __init__(
        self,
        inputs: tuple[str, ...] = (
            "lth_spending_zscore_30d",
            "lth_realized_profit_zscore",
            "exchange_inflow_zscore",
        ),
    ) -> None:
        super().__init__("supply_pressure", dict.fromkeys(inputs, 1.0))


class AbsorptionScoreFeature(WeightedCompositeFeature):
    def __init__(self) -> None:
        super().__init__(
            "btc_absorption_score",
            {"demand_pressure": 1.0, "supply_pressure": -1.0},
            history_scope=HistoryScope.POST_ETF,
        )
