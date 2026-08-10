from datetime import UTC, datetime, timedelta

import polars as pl

from investment.core.domain.experiment import DatasetSnapshot, ExperimentConfig
from investment.core.research.experiment import ExperimentRegistry, ExperimentRunner
from investment.core.research.stability import FeatureStabilityAnalyzer


def experiment_frame() -> pl.DataFrame:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    count = 730
    return pl.DataFrame(
        {
            "open_time": [start + timedelta(days=index) for index in range(count)],
            "signal": [float(index % 20) for index in range(count)],
            "forward_return_30d": [float(index % 20) / 100 for index in range(count)],
            "forward_mdd_30d": [-float(index % 10) / 100 for index in range(count)],
            "price_vs_ma_200": [(-1.0 if index % 3 == 0 else 1.0) for index in range(count)],
            "realized_vol_30d": [float(index % 17) for index in range(count)],
        }
    )


def test_snapshot_and_experiment_identity_are_reproducible(tmp_path) -> None:
    frame = experiment_frame()
    generated = datetime(2025, 1, 1, tzinfo=UTC)
    first_snapshot = DatasetSnapshot.from_frame(
        frame, sources=("fixture",), schema_version="v1", generated_at=generated
    )
    second_snapshot = DatasetSnapshot.from_frame(
        frame, sources=("fixture",), schema_version="v1", generated_at=generated
    )
    config = ExperimentConfig(
        "btc_absorption_v1",
        ("signal",),
        ("forward_return_30d",),
        datetime(2023, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 1, tzinfo=UTC),
        {"quantiles": 5},
    )
    runner = ExperimentRunner()
    first = runner.run(frame, config, first_snapshot, {"signal": "v1"}, created_at=generated)
    second = runner.run(frame, config, second_snapshot, {"signal": "v1"}, created_at=generated)
    assert first_snapshot.snapshot_id == second_snapshot.snapshot_id
    assert first.experiment_id == second.experiment_id
    assert first.results == second.results
    registry = ExperimentRegistry(tmp_path)
    assert registry.save(first).exists()
    assert registry.load(first.experiment_id)["experiment_id"] == first.experiment_id


def test_all_required_stability_groupings_are_available() -> None:
    frame = experiment_frame()
    analyzer = FeatureStabilityAnalyzer()
    assert len(analyzer.by_year(frame, "signal", "forward_return_30d").periods) == 2
    assert analyzer.rolling_years(frame, "signal", "forward_return_30d").periods
    assert len(analyzer.by_market_structure(frame, "signal", "forward_return_30d").periods) == 2
    assert len(analyzer.by_volatility(frame, "signal", "forward_return_30d").periods) == 2
