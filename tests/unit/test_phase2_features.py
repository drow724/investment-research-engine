import math
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from investment.bitcoin.features.derivatives.metrics import DerivativesFeatureFamily
from investment.bitcoin.features.divergence.pressure_price import (
    PressurePriceDivergenceFeature,
)
from investment.bitcoin.features.holder.metrics import HolderFeatureFamily
from investment.bitcoin.features.institutional.etf_flow import EtfFlowFeatureFamily
from investment.bitcoin.features.pressure.composite import (
    AbsorptionScoreFeature,
    DemandPressureFeature,
    SupplyPressureFeature,
)
from investment.core.data.point_in_time import PointInTimeDataset


def metric_frame(metrics: dict[str, list[float]], days: int) -> pl.DataFrame:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    for metric, values in metrics.items():
        for index in range(days):
            event = start + timedelta(days=index)
            rows.append(
                {
                    "dataset": "fixture",
                    "entity": "aggregate",
                    "metric": metric,
                    "event_time": event,
                    "available_at": event + timedelta(hours=1),
                    "ingested_at": datetime(2025, 1, 1, tzinfo=UTC),
                    "valid_from": event + timedelta(hours=1),
                    "value": values[index],
                    "unit": "native",
                    "source": "fixture",
                    "revision": 0,
                }
            )
    return pl.DataFrame(rows).sort("event_time")


def dataset(frame: pl.DataFrame) -> PointInTimeDataset:
    return PointInTimeDataset(frame, datetime(2025, 1, 2, tzinfo=UTC))


def test_etf_feature_family_values_and_aggregate_precedence() -> None:
    values = [float(index) for index in range(1, 71)]
    result = EtfFlowFeatureFamily().compute(dataset(metric_frame({"etf_net_flow": values}, 70)))
    assert result[4, "etf_net_flow_5d_sum"] == 15.0
    assert result[19, "etf_net_flow_20d_sum"] == 210.0
    assert result[9, "etf_flow_positive_days_10d"] == 10
    assert result[69, "etf_flow_zscore_60d"] is not None


def test_holder_and_derivatives_feature_families() -> None:
    days = 40
    holder = metric_frame(
        {
            "lth_supply": [100.0 + index for index in range(days)],
            "lth_spending": [10.0 + index % 5 for index in range(days)],
            "lth_realized_profit": [20.0 + index % 7 for index in range(days)],
            "lth_realized_loss": [5.0 + index % 3 for index in range(days)],
            "exchange_inflow": [30.0 + index % 11 for index in range(days)],
            "exchange_outflow": [25.0 + index % 9 for index in range(days)],
        },
        days,
    )
    holder_result = HolderFeatureFamily().compute(dataset(holder))
    assert holder_result[30, "lth_supply_change_30d"] == pytest.approx(0.3)
    assert holder_result[39, "exchange_inflow_zscore"] is not None
    assert "exchange_netflow_zscore" not in holder_result.columns

    derivatives = metric_frame(
        {
            "funding_rate": [0.001 * (index % 6) for index in range(days)],
            "open_interest": [1000.0 + index * 10 for index in range(days)],
        },
        days,
    )
    derivative_result = DerivativesFeatureFamily().compute(dataset(derivatives))
    assert derivative_result[7, "open_interest_change_7d"] == pytest.approx(70 / 1000)
    assert derivative_result[39, "funding_zscore_30d"] is not None


def test_composite_features_propagate_missing_and_calculate_exactly() -> None:
    frame = pl.DataFrame(
        {
            "open_time": [datetime(2024, 1, 1, tzinfo=UTC)] * 2,
            "etf_flow_zscore_20d": [2.0, None],
            "lth_spending_zscore_30d": [1.0, 1.0],
            "exchange_inflow_zscore": [0.5, 0.5],
        }
    )
    result = DemandPressureFeature().compute(frame)
    result = SupplyPressureFeature(
        ("lth_spending_zscore_30d", "exchange_inflow_zscore")
    ).compute(result)
    result = AbsorptionScoreFeature().compute(result)
    assert result[0, "demand_pressure"] == 2.0
    assert result[0, "supply_pressure"] == 1.5
    assert result[0, "btc_absorption_score"] == 0.5
    assert result[1, "btc_absorption_score"] is None


def test_positive_demand_with_weak_price_has_positive_divergence() -> None:
    frame = pl.DataFrame(
        {
            "demand_pressure": [0.0, 0.0, 2.0],
            "close": [100.0, 100.0, 99.0],
        }
    )
    result = PressurePriceDivergenceFeature("demand_pressure", 2).compute(frame)
    assert math.isclose(result[2, "demand_price_divergence_2d"], 2.01)
