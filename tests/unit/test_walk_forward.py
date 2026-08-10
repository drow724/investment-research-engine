from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from investment.core.research.walk_forward import WalkForwardConfig, WalkForwardSplitter


@pytest.mark.parametrize("mode", ["rolling", "expanding"])
def test_walk_forward_periods_never_overlap(mode: str) -> None:
    start = datetime(2018, 1, 1, tzinfo=UTC)
    times = [start + timedelta(days=index) for index in range(365 * 8)]
    frame = pl.DataFrame({"open_time": times, "value": range(len(times))})
    splitter = WalkForwardSplitter(
        WalkForwardConfig(train_years=4, validation_years=1, test_years=1, mode=mode)  # type: ignore[arg-type]
    )
    splits = splitter.split(frame)
    assert splits
    for split in splits:
        assert split.train.get_column("open_time").max() < split.validation.get_column(
            "open_time"
        ).min()
        assert split.validation.get_column("open_time").max() < split.test.get_column(
            "open_time"
        ).min()
