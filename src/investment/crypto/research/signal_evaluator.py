"""Dependence-aware evaluation for frozen cross-sectional research signals."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from math import isfinite
from statistics import fmean, median

import polars as pl
from scipy import stats


@dataclass(frozen=True, slots=True)
class SignalEvaluation:
    signal_id: str
    horizon_minutes: int
    raw_rows: int
    unique_timestamps: int
    unique_assets: int
    non_overlapping_time_blocks: int
    top_k_rows: int
    top_k_episodes: int
    top_asset_share: float
    pooled_spearman_ic: float | None
    mean_cross_sectional_rank_ic: float | None
    top_k_mean_return: float
    top_k_median_return: float
    top_k_hit_rate: float
    top_k_after_cost_mean_return: float
    top_k_universe_relative_mean_return: float
    top_k_btc_relative_mean_return: float | None
    top_k_mean_mfe: float | None
    top_k_mean_mae: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class CandidateSignalEvaluator:
    """Evaluate rankings without pretending overlapping rows are independent."""

    def evaluate(
        self,
        signals: pl.DataFrame,
        snapshots: pl.DataFrame,
        outcomes: pl.DataFrame,
        *,
        top_k: int = 3,
        round_trip_cost: float = 0.002,
    ) -> tuple[SignalEvaluation, ...]:
        if top_k <= 0 or round_trip_cost < 0:
            raise ValueError("top_k must be positive and cost cannot be negative")
        required_outcomes = {
            "snapshot_id",
            "horizon_minutes",
            "status",
            "forward_return",
            "mfe",
            "mae",
        }
        missing = required_outcomes.difference(outcomes.columns)
        if missing:
            raise ValueError(f"missing outcome columns: {sorted(missing)}")

        completed = outcomes.filter(pl.col("status") == "COMPLETED")
        joined = signals.join(completed, on="snapshot_id", how="inner")
        btc = (
            snapshots.with_columns(
                pl.col("decision_time").cast(pl.Datetime("us", "UTC"))
            )
            .filter(pl.col("market") == "BTCKRW")
            .select("snapshot_id", "decision_time")
            .join(completed.select("snapshot_id", "horizon_minutes", "forward_return"),
                  on="snapshot_id", how="inner")
            .rename({"forward_return": "btc_forward_return"})
            .select("decision_time", "horizon_minutes", "btc_forward_return")
        )
        joined = joined.join(btc, on=["decision_time", "horizon_minutes"], how="left")
        joined = joined.with_columns(
            pl.col("forward_return").median().over(
                ["signal_id", "decision_time", "horizon_minutes"]
            ).alias("cohort_median_forward_return")
        )

        results: list[SignalEvaluation] = []
        keys = joined.select("signal_id", "horizon_minutes").unique().sort(
            ["signal_id", "horizon_minutes"]
        )
        for key in keys.iter_rows(named=True):
            group = joined.filter(
                (pl.col("signal_id") == key["signal_id"])
                & (pl.col("horizon_minutes") == key["horizon_minutes"])
            )
            top = group.filter(pl.col("rank") <= top_k).sort(
                ["market", "decision_time"]
            )
            if top.is_empty():
                continue
            returns = [float(value) for value in top.get_column("forward_return")]
            relatives = [
                float(row["forward_return"] - row["cohort_median_forward_return"])
                for row in top.select("forward_return", "cohort_median_forward_return").iter_rows(
                    named=True
                )
            ]
            btc_relatives = [
                float(row["forward_return"] - row["btc_forward_return"])
                for row in top.select("forward_return", "btc_forward_return")
                .drop_nulls()
                .iter_rows(named=True)
            ]
            asset_counts = top.group_by("market").len().sort("len", descending=True)
            results.append(
                SignalEvaluation(
                    signal_id=str(key["signal_id"]),
                    horizon_minutes=int(key["horizon_minutes"]),
                    raw_rows=group.height,
                    unique_timestamps=group.get_column("decision_time").n_unique(),
                    unique_assets=group.get_column("market").n_unique(),
                    non_overlapping_time_blocks=_non_overlapping_blocks(
                        group.get_column("decision_time").unique().sort().to_list(),
                        int(key["horizon_minutes"]),
                    ),
                    top_k_rows=top.height,
                    top_k_episodes=_episodes(top),
                    top_asset_share=float(asset_counts[0, "len"] / top.height),
                    pooled_spearman_ic=_spearman(group),
                    mean_cross_sectional_rank_ic=_mean_cross_sectional_ic(group),
                    top_k_mean_return=fmean(returns),
                    top_k_median_return=median(returns),
                    top_k_hit_rate=fmean(value > 0 for value in returns),
                    top_k_after_cost_mean_return=fmean(returns) - round_trip_cost,
                    top_k_universe_relative_mean_return=fmean(relatives),
                    top_k_btc_relative_mean_return=(
                        fmean(btc_relatives) if btc_relatives else None
                    ),
                    top_k_mean_mfe=_optional_mean(top, "mfe"),
                    top_k_mean_mae=_optional_mean(top, "mae"),
                )
            )
        return tuple(results)


def _spearman(frame: pl.DataFrame) -> float | None:
    if frame.height < 3:
        return None
    value = float(
        stats.spearmanr(
            frame.get_column("value").to_numpy(),
            frame.get_column("forward_return").to_numpy(),
        ).statistic
    )
    return value if isfinite(value) else None


def _mean_cross_sectional_ic(frame: pl.DataFrame) -> float | None:
    values = []
    for cohort in frame.partition_by("decision_time", maintain_order=True):
        value = _spearman(cohort)
        if value is not None:
            values.append(value)
    return fmean(values) if values else None


def _optional_mean(frame: pl.DataFrame, column: str) -> float | None:
    values = [float(value) for value in frame.get_column(column).drop_nulls()]
    return fmean(values) if values else None


def _episodes(top: pl.DataFrame) -> int:
    episodes = 0
    maximum_gap = timedelta(minutes=20)
    for asset in top.get_column("market").unique().to_list():
        times = (
            top.filter(pl.col("market") == asset)
            .get_column("decision_time")
            .unique()
            .sort()
            .to_list()
        )
        previous = None
        for current in times:
            if previous is None or current - previous > maximum_gap:
                episodes += 1
            previous = current
    return episodes


def _non_overlapping_blocks(times: list[datetime], horizon_minutes: int) -> int:
    count = 0
    block_end = None
    width = timedelta(minutes=horizon_minutes)
    for current in times:
        if block_end is None or current >= block_end:
            count += 1
            block_end = current + width
    return count
