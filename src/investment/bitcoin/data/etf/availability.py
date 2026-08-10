from investment.core.data.availability import PublishedTimestampPolicy


class EtfPublicationAvailabilityPolicy(PublishedTimestampPolicy):
    """ETF flows become usable at the supplied vendor publication timestamp."""

    def __init__(self) -> None:
        super().__init__(field="published_at")
