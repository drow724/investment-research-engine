import math

import numpy as np
import pytest

from investment.bitcoin.features.momentum import PriceVsMovingAverageFeature, ReturnFeature
from investment.bitcoin.features.volatility import RealizedVolatilityFeature
from investment.bitcoin.features.volume import (
    VolumeChangeFeature,
    VolumeMovingAverageFeature,
    VolumeZScoreFeature,
)
from investment.core.data.point_in_time import PointInTimeDataset
from investment.core.feature.pipeline import FeaturePipeline


def test_price_features_use_only_historical_rows(btc_dataset: PointInTimeDataset) -> None:
    result = FeaturePipeline(
        [ReturnFeature(1), PriceVsMovingAverageFeature(3)]
    ).compute(btc_dataset)
    assert result[1, "return_1d"] == 103 / 101 - 1
    assert result[2, "price_vs_ma_3"] == 102 / np.mean([101, 103, 102]) - 1


def test_realized_volatility_is_annualized(btc_dataset: PointInTimeDataset) -> None:
    result = RealizedVolatilityFeature(3).compute(btc_dataset)
    daily_returns = np.array([103 / 101 - 1, 102 / 103 - 1, 106 / 102 - 1])
    expected = np.std(daily_returns, ddof=1) * math.sqrt(365)
    assert math.isclose(result[3, "realized_vol_3d"], expected)


def test_volume_features(btc_dataset: PointInTimeDataset) -> None:
    result = FeaturePipeline(
        [VolumeChangeFeature(), VolumeMovingAverageFeature(3), VolumeZScoreFeature(3)]
    ).compute(btc_dataset)
    assert result[1, "volume_change_1d"] == pytest.approx(0.2)
    assert result[2, "volume_ma_3"] == 11.0
    assert result[2, "volume_zscore_3d"] == 0.0


def test_features_cannot_call_provider_by_contract() -> None:
    # Runtime-facing feature objects accept only PointInTimeDataset.
    annotation = ReturnFeature.compute.__annotations__["dataset"]
    assert annotation == "PointInTimeDataset" or annotation is PointInTimeDataset
