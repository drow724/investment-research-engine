from datetime import UTC, datetime

from investment.application.services.metric_data_service import MetricDataService
from investment.bitcoin.data.etf.availability import EtfPublicationAvailabilityPolicy
from investment.bitcoin.data.etf.normalizer import BitcoinEtfFlowNormalizer
from investment.core.data.provider import RawMetricBatch
from investment.core.data.storage import MetricParquetStorage, RawMetricJsonStorage


class FixtureEtfProvider:
    def fetch(self, start: datetime, end: datetime) -> RawMetricBatch:
        return RawMetricBatch(
            "etf",
            start,
            end,
            "fixture",
            [
                {
                    "event_time": "2024-01-01T00:00:00Z",
                    "published_at": "2024-01-02T00:00:00Z",
                    "fund": "IBIT",
                    "value": 10,
                }
            ],
        )


def test_etf_sync_persists_raw_and_idempotent_normalized_data(tmp_path) -> None:
    normalized_storage = MetricParquetStorage(tmp_path / "normalized")
    service = MetricDataService(
        FixtureEtfProvider(),
        BitcoinEtfFlowNormalizer(EtfPublicationAvailabilityPolicy()),
        RawMetricJsonStorage(tmp_path / "raw"),
        normalized_storage,
    )
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 3, tzinfo=UTC)
    first = service.sync(start, end)
    second = service.sync(start, end)
    assert first.raw_path == second.raw_path
    assert first.normalized_path == second.normalized_path
    assert normalized_storage.read("etf").height == 1
