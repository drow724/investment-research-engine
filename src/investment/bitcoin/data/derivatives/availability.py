from investment.core.data.availability import PublishedTimestampPolicy


class DerivativesPublicationAvailabilityPolicy(PublishedTimestampPolicy):
    """Use the exchange observation/publication timestamp."""
