from datetime import UTC, datetime

import polars as pl

from investment.core.data.missing import MissingDataPolicy, apply_missing_policy
from investment.core.data.point_in_time import PointInTimeDataset


def test_latest_revision_known_at_query_time_is_selected() -> None:
    event = datetime(2024, 1, 1, tzinfo=UTC)
    frame = pl.DataFrame(
        {
            "dataset": ["onchain", "onchain"],
            "entity": ["aggregate", "aggregate"],
            "metric": ["lth_supply", "lth_supply"],
            "event_time": [event, event],
            "available_at": [
                datetime(2024, 1, 2, tzinfo=UTC),
                datetime(2024, 2, 1, tzinfo=UTC),
            ],
            "valid_from": [
                datetime(2024, 1, 2, tzinfo=UTC),
                datetime(2024, 2, 1, tzinfo=UTC),
            ],
            "value": [100.0, 110.0],
            "source": ["vendor", "vendor"],
            "revision": [0, 1],
        }
    )
    january = PointInTimeDataset(frame, datetime(2024, 1, 15, tzinfo=UTC)).frame()
    february = PointInTimeDataset(frame, datetime(2024, 2, 2, tzinfo=UTC)).frame()
    assert january.get_column("value").to_list() == [100.0]
    assert february.get_column("value").to_list() == [110.0]


def test_missing_data_is_not_zero_filled_by_default() -> None:
    frame = pl.DataFrame({"value": [1.0, None, 3.0]})
    unchanged = apply_missing_policy(frame, ["value"], MissingDataPolicy.NO_FILL)
    assert unchanged.get_column("value").to_list() == [1.0, None, 3.0]
    assert apply_missing_policy(frame, ["value"], MissingDataPolicy.ZERO)[1, "value"] == 0.0
