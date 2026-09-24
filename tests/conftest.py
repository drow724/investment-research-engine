from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from investment.core.data.point_in_time import PointInTimeDataset


@pytest.fixture(autouse=True)
def isolate_runtime_from_local_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tests must never initialize the developer's live Docker bind-mounted state."""

    monkeypatch.setenv("INVESTMENT_CRYPTO_PAPER_DATABASE", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv(
        "INVESTMENT_CRYPTO_OBSERVATION_DATABASE", str(tmp_path / "observation.sqlite3")
    )
    monkeypatch.setenv(
        "INVESTMENT_CRYPTO_DERIVATIVES_DATABASE", str(tmp_path / "derivatives.sqlite3")
    )
    monkeypatch.setenv("INVESTMENT_RUNTIME_STATE_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("INVESTMENT_CRYPTO_STRATEGY_REVIEW_ROOT", str(tmp_path / "review"))
    monkeypatch.setenv("INVESTMENT_RUNTIME_DYNAMIC_STRATEGY_VERSION", "dynamic-intraday-v2.1")
    monkeypatch.setenv("INVESTMENT_RUNTIME_DYNAMIC_PAPER_PORTFOLIO_ID", "")
    monkeypatch.setenv("INVESTMENT_RUNTIME_DYNAMIC_PAPER_EXECUTE", "false")
    monkeypatch.setenv("INVESTMENT_RUNTIME_OBSERVATION_EXPERIMENT_ID", "")
    monkeypatch.setenv("INVESTMENT_RUNTIME_V28_PAPER_EXPERIMENT_PREFIX", "")
    monkeypatch.setenv("INVESTMENT_RUNTIME_V29_PAPER_EXPERIMENT_PREFIX", "")
    monkeypatch.setenv("INVESTMENT_RUNTIME_OBSERVATION_DRAIN_EXPERIMENT_IDS_JSON", "[]")
    monkeypatch.setenv("INVESTMENT_RUNTIME_DERIVATIVES_WEBSOCKET_ENABLED", "false")
    monkeypatch.setenv("INVESTMENT_RUNTIME_SHADOW_DYNAMIC_STRATEGY_VERSION", "")
    monkeypatch.setenv("INVESTMENT_RUNTIME_SHADOW_DYNAMIC_PAPER_PORTFOLIO_ID", "")
    monkeypatch.setenv("INVESTMENT_RUNTIME_SHADOW_OBSERVATION_EXPERIMENT_ID", "")


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
