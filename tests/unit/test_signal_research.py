from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from investment.crypto.research.hypothesis_registry import (
    HypothesisRegistry,
    HypothesisStatus,
    ResearchHypothesis,
)
from investment.crypto.research.signal_evaluator import CandidateSignalEvaluator
from investment.crypto.research.signal_registry import v29_momentum_signal_registry


def _snapshots() -> pl.DataFrame:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for cohort in range(3):
        at = start + timedelta(minutes=15 * cohort)
        for index, market in enumerate(("BTCKRW", "ETHKRW", "XRPKRW")):
            rows.append(
                {
                    "snapshot_id": f"{cohort}-{market}",
                    "decision_time": at,
                    "market": market,
                    "eligible": 1,
                    "score": 0.9 - index * 0.2,
                    "raw_score": 0.9 - index * 0.2,
                    "momentum_1h": 0.01 + cohort * 0.001 - index * 0.002,
                    "momentum_4h": 0.02 + cohort * 0.002 - index * 0.003,
                    "momentum_24h": 0.03 - index * 0.004,
                    "volatility": 0.002 + index * 0.0002,
                    "liquidity": 1_000_000 - index,
                }
            )
    return pl.DataFrame(rows)


def test_v29_registry_materializes_independent_ranked_signals() -> None:
    materialized = v29_momentum_signal_registry().materialize(_snapshots())

    assert set(materialized.get_column("signal_id")) == {
        "m0_v29_score",
        "m1_btc_relative_1h",
        "m1_universe_relative_1h",
        "m2_acceleration_1h_vs_4h",
        "m3_risk_adjusted_4h",
    }
    assert materialized.group_by("signal_id").len().get_column("len").to_list() == [9] * 5
    assert materialized.filter(pl.col("signal_id") == "m0_v29_score").filter(
        pl.col("market") == "BTCKRW"
    ).get_column("rank").to_list() == [1.0, 1.0, 1.0]


def test_evaluator_discloses_overlap_and_episode_counts() -> None:
    snapshots = _snapshots()
    signals = v29_momentum_signal_registry().materialize(snapshots).filter(
        pl.col("signal_id") == "m0_v29_score"
    )
    outcomes = pl.DataFrame(
        [
            {
                "snapshot_id": row["snapshot_id"],
                "horizon_minutes": 60,
                "status": "COMPLETED",
                "forward_return": 0.03 - float(row["score"]) * 0.01,
                "mfe": 0.04,
                "mae": -0.01,
            }
            for row in snapshots.iter_rows(named=True)
        ]
    )

    result = CandidateSignalEvaluator().evaluate(
        signals, snapshots, outcomes, top_k=1, round_trip_cost=0.002
    )[0]

    assert result.raw_rows == 9
    assert result.unique_timestamps == 3
    assert result.non_overlapping_time_blocks == 1
    assert result.top_k_rows == 3
    assert result.top_k_episodes == 1
    assert result.top_asset_share == 1.0
    assert result.top_k_after_cost_mean_return == pytest.approx(
        result.top_k_mean_return - 0.002
    )


def test_hypothesis_oos_can_only_be_exposed_once(tmp_path) -> None:
    registry = HypothesisRegistry(tmp_path)
    hypothesis = ResearchHypothesis(
        "h-m1-001",
        "BTC-relative momentum has positive cross-sectional IC.",
        ("m1_btc_relative_1h",),
        60,
        "v29_scored_liquidity_top20",
        "mean_cross_sectional_rank_ic",
        ("top_k_universe_relative_mean_return",),
        "2026-08-01/2026-08-31",
        "2026-09-01/2026-09-15",
        "2026-09-16/2026-09-30",
        HypothesisStatus.OOS_PENDING,
        datetime(2026, 9, 1, tzinfo=UTC),
    )
    registry.create(hypothesis)

    exposed = registry.expose_oos(hypothesis.hypothesis_id)

    assert exposed.oos_exposed_at is not None
    with pytest.raises(ValueError, match="already been consumed"):
        registry.expose_oos(hypothesis.hypothesis_id)
