from datetime import UTC, datetime

import polars as pl
import pytest

from investment.core.data.point_in_time import PointInTimeDataset


def test_future_publication_is_not_visible() -> None:
    frame = pl.DataFrame(
        {
            "open_time": [
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2024, 1, 2, tzinfo=UTC),
            ],
            "available_at": [
                datetime(2024, 1, 2, tzinfo=UTC),
                datetime(2024, 2, 1, tzinfo=UTC),
            ],
            "close": [100.0, 999.0],
        }
    )
    dataset = PointInTimeDataset(frame, datetime(2024, 1, 10, tzinfo=UTC))
    assert dataset.frame().get_column("close").to_list() == [100.0]


def test_dataset_cannot_be_widened(btc_dataset: PointInTimeDataset) -> None:
    with pytest.raises(ValueError, match="cannot widen"):
        btc_dataset.at(datetime(2025, 1, 1, tzinfo=UTC))
