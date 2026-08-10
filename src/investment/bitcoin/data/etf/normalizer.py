from investment.bitcoin.data.normalization import LongFormMetricNormalizer
from investment.core.data.availability import DataAvailabilityPolicy


class BitcoinEtfFlowNormalizer(LongFormMetricNormalizer):
    def __init__(self, availability_policy: DataAvailabilityPolicy) -> None:
        super().__init__(
            "etf",
            availability_policy,
            default_metric="etf_net_flow",
            default_unit="USD",
        )
