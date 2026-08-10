from datetime import UTC, datetime

import pytest

from investment.core.domain.observation import MarketObservation


def test_observation_rejects_naive_datetime() -> None:
    aware = datetime(2024, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone-aware"):
        MarketObservation("BTC", "close", datetime(2024, 1, 1), aware, aware, 1.0, "test")
