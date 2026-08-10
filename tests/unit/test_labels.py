import math
from datetime import UTC, datetime, timedelta

import polars as pl

from investment.core.data.point_in_time import PointInTimeDataset
from investment.core.labels.forward import ForwardLabelGenerator


def test_forward_returns_and_path_max_drawdown() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    times = [start + timedelta(days=index) for index in range(4)]
    frame = pl.DataFrame(
        {"open_time": times, "available_at": times, "close": [100.0, 120.0, 90.0, 110.0]}
    )
    result = ForwardLabelGenerator(return_horizons=(2,), mdd_horizons=(2,)).compute(
        PointInTimeDataset.latest(frame)
    )
    assert math.isclose(result[0, "forward_return_2d"], -0.1)
    assert math.isclose(result[0, "forward_mdd_2d"], -0.25)
    assert result[2, "forward_return_2d"] is None
    assert result[2, "forward_mdd_2d"] is None
