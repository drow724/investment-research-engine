from investment.bitcoin.data.normalization import LongFormMetricNormalizer
from investment.core.data.availability import DataAvailabilityPolicy


class BitcoinOnChainNormalizer(LongFormMetricNormalizer):
    def __init__(self, availability_policy: DataAvailabilityPolicy) -> None:
        super().__init__(
            "onchain",
            availability_policy,
            non_negative_metrics=frozenset(
                {
                    "lth_supply",
                    "lth_spending",
                    "lth_realized_profit",
                    "lth_realized_loss",
                    "exchange_inflow",
                    "exchange_outflow",
                }
            ),
        )
