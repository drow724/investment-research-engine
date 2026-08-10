from investment.bitcoin.data.normalization import LongFormMetricNormalizer
from investment.core.data.availability import DataAvailabilityPolicy


class BitcoinDerivativesNormalizer(LongFormMetricNormalizer):
    def __init__(self, availability_policy: DataAvailabilityPolicy) -> None:
        super().__init__("derivatives", availability_policy)
