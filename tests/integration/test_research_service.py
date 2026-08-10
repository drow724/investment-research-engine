from datetime import UTC, datetime, timedelta

import polars as pl

from investment.application.services.research_service import ResearchService
from investment.core.data.storage import NormalizedParquetStorage


def test_research_service_runs_the_owned_pipeline(tmp_path) -> None:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    count = 420
    open_times = [start + timedelta(days=index) for index in range(count)]
    frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"] * count,
            "open_time": open_times,
            "available_at": [value + timedelta(days=1) for value in open_times],
            "ingested_at": [datetime(2022, 1, 1, tzinfo=UTC)] * count,
            "open": [100.0 + index for index in range(count)],
            "high": [101.0 + index for index in range(count)],
            "low": [99.0 + index for index in range(count)],
            "close": [100.0 + index + (index % 7) for index in range(count)],
            "volume": [1000.0 + (index % 31) * 10 for index in range(count)],
            "source": ["fixture"] * count,
        }
    )
    storage = NormalizedParquetStorage(tmp_path)
    storage.save("BTCUSDT", frame)

    result = ResearchService(storage).evaluate_feature(
        symbol="BTCUSDT",
        as_of=datetime(2022, 1, 1, tzinfo=UTC),
        feature="return_30d",
        label="forward_return_30d",
        quantiles=5,
    )

    assert result.feature == "return_30d"
    assert result.label == "forward_return_30d"
    assert result.count > 300
    assert len(result.quantiles) == 5
