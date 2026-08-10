"""Compose normalized feature families without knowing vendor payload shapes."""

from datetime import datetime

import polars as pl

from investment.bitcoin.features.defaults import default_bitcoin_features
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
from investment.bitcoin.research.hypotheses import (
    distribution_risk_feature,
    weak_hand_capitulation_feature,
)
from investment.core.data.point_in_time import PointInTimeDataset
from investment.core.feature.pipeline import FeaturePipeline


class BitcoinIntelligenceBuilder:
    def build(
        self,
        price: pl.DataFrame,
        as_of: datetime,
        *,
        etf: pl.DataFrame | None = None,
        onchain: pl.DataFrame | None = None,
        derivatives: pl.DataFrame | None = None,
    ) -> pl.DataFrame:
        price_dataset = PointInTimeDataset(price, as_of)
        result = price_dataset.frame().select("open_time", "close")
        result = result.join(
            FeaturePipeline(default_bitcoin_features()).compute(price_dataset),
            on="open_time",
            how="left",
        )
        if etf is not None:
            result = result.join(
                EtfFlowFeatureFamily().compute(PointInTimeDataset(etf, as_of)),
                on="open_time",
                how="left",
            )
        if onchain is not None:
            result = result.join(
                HolderFeatureFamily().compute(PointInTimeDataset(onchain, as_of)),
                on="open_time",
                how="left",
            )
        if derivatives is not None:
            result = result.join(
                DerivativesFeatureFamily().compute(PointInTimeDataset(derivatives, as_of)),
                on="open_time",
                how="left",
            )
        if "etf_flow_zscore_20d" in result.columns:
            result = DemandPressureFeature().compute(result)
        supply_inputs = tuple(
            name
            for name in (
                "lth_spending_zscore_30d",
                "lth_realized_profit_zscore",
                "exchange_inflow_zscore",
            )
            if name in result.columns
        )
        if supply_inputs:
            result = SupplyPressureFeature(supply_inputs).compute(result)
        if {"demand_pressure", "supply_pressure"}.issubset(result.columns):
            result = AbsorptionScoreFeature().compute(result)
            for days in (7, 30):
                result = PressurePriceDivergenceFeature("demand_pressure", days).compute(result)
            result = PressurePriceDivergenceFeature("supply_pressure", 30).compute(result)
        for hypothesis_feature in (
            weak_hand_capitulation_feature(),
            distribution_risk_feature(),
        ):
            if set(hypothesis_feature.weights).issubset(result.columns):
                result = hypothesis_feature.compute(result)
        return result
