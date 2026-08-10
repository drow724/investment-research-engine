from investment.core.data.availability import PublishedTimestampPolicy


class OnChainPublicationAvailabilityPolicy(PublishedTimestampPolicy):
    """Require the vendor's explicit publication time for revision-prone metrics."""
