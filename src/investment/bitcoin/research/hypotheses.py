"""Named, falsifiable interpretations rather than asserted market truths."""

from investment.bitcoin.features.pressure.composite import (
    HistoryScope,
    WeightedCompositeFeature,
)
from investment.core.domain.experiment import Hypothesis

ABSORPTION = Hypothesis(
    "btc_absorption_v1",
    "Demand remains strong relative to observed holder and exchange supply pressure.",
    "btc_absorption_score",
)

WEAK_HAND_CAPITULATION = Hypothesis(
    "btc_weak_hand_capitulation_v1",
    "Realized loss and exchange pressure coexist with ETF demand and price support.",
    "weak_hand_capitulation_score",
)

DISTRIBUTION_RISK = Hypothesis(
    "btc_distribution_risk_v1",
    "Price strength coexists with realized profit, inflows, and slowing ETF demand.",
    "distribution_risk_score",
)

HYPOTHESES = {item.name: item for item in (ABSORPTION, WEAK_HAND_CAPITULATION, DISTRIBUTION_RISK)}


def weak_hand_capitulation_feature() -> WeightedCompositeFeature:
    return WeightedCompositeFeature(
        "weak_hand_capitulation_score",
        {
            "lth_realized_loss_zscore": 1.0,
            "exchange_inflow_zscore": 1.0,
            "etf_flow_zscore_20d": 1.0,
            "return_30d": 1.0,
        },
        history_scope=HistoryScope.POST_ETF,
    )


def distribution_risk_feature() -> WeightedCompositeFeature:
    return WeightedCompositeFeature(
        "distribution_risk_score",
        {
            "return_30d": 1.0,
            "lth_realized_profit_zscore": 1.0,
            "exchange_inflow_zscore": 1.0,
            "etf_flow_acceleration": -1.0,
        },
        history_scope=HistoryScope.POST_ETF,
    )
