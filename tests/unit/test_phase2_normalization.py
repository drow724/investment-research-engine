from datetime import UTC, datetime

import pytest

from investment.bitcoin.data.etf.availability import EtfPublicationAvailabilityPolicy
from investment.bitcoin.data.etf.normalizer import BitcoinEtfFlowNormalizer
from investment.bitcoin.data.onchain.availability import OnChainPublicationAvailabilityPolicy
from investment.bitcoin.data.onchain.normalizer import BitcoinOnChainNormalizer
from investment.core.data.provider import RawMetricBatch
from investment.core.data.quality import DataQualityError


def test_etf_normalizer_preserves_publication_and_revision_times() -> None:
    batch = RawMetricBatch(
        "etf",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
        "fixture",
        [
            {
                "event_time": "2024-01-01T00:00:00Z",
                "published_at": "2024-01-02T03:00:00Z",
                "fund": "IBIT",
                "value": 100.0,
                "currency": "USD",
                "revision": 2,
            }
        ],
    )
    result = BitcoinEtfFlowNormalizer(EtfPublicationAvailabilityPolicy()).normalize(
        batch, datetime(2024, 1, 2, 4, tzinfo=UTC)
    )
    assert result[0, "available_at"] == datetime(2024, 1, 2, 3, tzinfo=UTC)
    assert result[0, "valid_from"] == result[0, "available_at"]
    assert result[0, "revision"] == 2


def test_quality_validation_rejects_impossible_negative_supply() -> None:
    batch = RawMetricBatch(
        "onchain",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
        "fixture",
        [
            {
                "event_time": "2024-01-01T00:00:00Z",
                "published_at": "2024-01-02T00:00:00Z",
                "metric": "lth_supply",
                "value": -1,
            }
        ],
    )
    with pytest.raises(DataQualityError, match="negative value"):
        BitcoinOnChainNormalizer(OnChainPublicationAvailabilityPolicy()).normalize(batch)


def test_quality_validation_rejects_duplicate_revision_keys() -> None:
    record = {
        "event_time": "2024-01-01T00:00:00Z",
        "published_at": "2024-01-02T00:00:00Z",
        "fund": "IBIT",
        "value": 1,
    }
    batch = RawMetricBatch(
        "etf",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
        "fixture",
        [record, record],
    )
    with pytest.raises(DataQualityError, match="duplicate"):
        BitcoinEtfFlowNormalizer(EtfPublicationAvailabilityPolicy()).normalize(batch)


def test_quality_validation_rejects_out_of_order_metric_series() -> None:
    batch = RawMetricBatch(
        "etf",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 4, tzinfo=UTC),
        "fixture",
        [
            {
                "event_time": "2024-01-03T00:00:00Z",
                "published_at": "2024-01-03T01:00:00Z",
                "fund": "IBIT",
                "value": 1,
            },
            {
                "event_time": "2024-01-01T00:00:00Z",
                "published_at": "2024-01-01T01:00:00Z",
                "fund": "IBIT",
                "value": 2,
            },
        ],
    )
    with pytest.raises(DataQualityError, match="must be sorted"):
        BitcoinEtfFlowNormalizer(EtfPublicationAvailabilityPolicy()).normalize(batch)
