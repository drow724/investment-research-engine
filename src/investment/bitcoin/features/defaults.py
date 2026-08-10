"""The deliberately small Phase 1 BTC feature set."""

from investment.bitcoin.features.momentum import PriceVsMovingAverageFeature, ReturnFeature
from investment.bitcoin.features.volatility import RealizedVolatilityFeature
from investment.bitcoin.features.volume import (
    VolumeChangeFeature,
    VolumeMovingAverageFeature,
    VolumeZScoreFeature,
)
from investment.core.feature.base import Feature


def default_bitcoin_features() -> list[Feature]:
    return [
        *(ReturnFeature(days) for days in (1, 7, 30, 90)),
        *(PriceVsMovingAverageFeature(days) for days in (20, 50, 200)),
        *(RealizedVolatilityFeature(days) for days in (7, 30, 90)),
        VolumeChangeFeature(),
        VolumeMovingAverageFeature(),
        VolumeZScoreFeature(),
    ]
