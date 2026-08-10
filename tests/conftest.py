from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from investment.core.data.point_in_time import PointInTimeDataset


@pytest.fixture
def btc_frame() -> pl.DataFrame:
    return pl.read_csv(
        Path(__file__).parent / "fixtures" / "btc_daily.csv",
        try_parse_dates=True,
    ).with_columns(
        pl.col("open_time").dt.convert_time_zone("UTC"),
        pl.col("available_at").dt.convert_time_zone("UTC"),
        pl.col("ingested_at").dt.convert_time_zone("UTC"),
    )


@pytest.fixture
def btc_dataset(btc_frame: pl.DataFrame) -> PointInTimeDataset:
    return PointInTimeDataset(btc_frame, datetime(2024, 1, 20, tzinfo=UTC))
