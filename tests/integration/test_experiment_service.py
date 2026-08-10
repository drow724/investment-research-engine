from datetime import UTC, datetime, timedelta

import polars as pl

from investment.application.services.experiment_service import ExperimentService
from investment.bitcoin.data.etf.availability import EtfPublicationAvailabilityPolicy
from investment.bitcoin.data.etf.normalizer import BitcoinEtfFlowNormalizer
from investment.bitcoin.data.onchain.availability import OnChainPublicationAvailabilityPolicy
from investment.bitcoin.data.onchain.normalizer import BitcoinOnChainNormalizer
from investment.core.data.provider import RawMetricBatch
from investment.core.data.storage import MetricParquetStorage, NormalizedParquetStorage
from investment.core.research.experiment import ExperimentRegistry


def test_experiment_service_builds_and_registers_absorption_experiment(tmp_path) -> None:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    count = 600
    times = [start + timedelta(days=index) for index in range(count)]
    price = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"] * count,
            "open_time": times,
            "available_at": [value + timedelta(hours=23) for value in times],
            "ingested_at": [datetime(2025, 1, 1, tzinfo=UTC)] * count,
            "open": [20_000.0 + index for index in range(count)],
            "high": [20_100.0 + index for index in range(count)],
            "low": [19_900.0 + index for index in range(count)],
            "close": [20_000.0 + index + (index % 13) * 5 for index in range(count)],
            "volume": [1000.0 + index % 29 for index in range(count)],
            "source": ["fixture_price"] * count,
        }
    )
    price_storage = NormalizedParquetStorage(tmp_path / "normalized")
    price_storage.save("BTCUSDT", price)
    metric_storage = MetricParquetStorage(tmp_path / "normalized")

    metric_start = start + timedelta(days=180)
    metric_count = 420
    etf_records = []
    onchain_records = []
    for index in range(metric_count):
        event = metric_start + timedelta(days=index)
        publication = event + timedelta(hours=1)
        etf_records.append(
            {
                "event_time": event,
                "published_at": publication,
                "entity": "aggregate",
                "value": float((index % 17) - 8) * 1_000_000,
            }
        )
        for metric, value in (
            ("lth_spending", 100.0 + index % 11),
            ("lth_realized_profit", 80.0 + index % 7),
            ("exchange_inflow", 90.0 + index % 13),
        ):
            onchain_records.append(
                {
                    "event_time": event,
                    "published_at": publication,
                    "metric": metric,
                    "value": value,
                }
            )
    metric_end = metric_start + timedelta(days=metric_count)
    etf = BitcoinEtfFlowNormalizer(EtfPublicationAvailabilityPolicy()).normalize(
        RawMetricBatch("etf", metric_start, metric_end, "fixture_etf", etf_records)
    )
    onchain = BitcoinOnChainNormalizer(OnChainPublicationAvailabilityPolicy()).normalize(
        RawMetricBatch(
            "onchain", metric_start, metric_end, "fixture_onchain", onchain_records
        )
    )
    metric_storage.save("etf", etf)
    metric_storage.save("onchain", onchain)

    registry = ExperimentRegistry(tmp_path / "experiments")
    run = ExperimentService(price_storage, metric_storage, registry).run(
        hypothesis="btc_absorption_v1",
        features=("btc_absorption_score",),
        labels=("forward_return_30d", "forward_return_90d", "forward_return_180d"),
        start=metric_start,
        end=metric_end,
        quantiles=5,
    )
    assert run.status == "COMPLETED"
    assert set(run.results) == {
        "forward_return_30d",
        "forward_return_90d",
        "forward_return_180d",
    }
    assert registry.load(run.experiment_id)["dataset_snapshot"]["row_count"] == metric_count
