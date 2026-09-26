#!/usr/bin/env python3
"""Read-only M0-M3 candidate-cohort research against PostgreSQL observations.

This produces descriptive research evidence only.  It does not tune parameters,
expose a reserved OOS period, or modify the V2.9 paper strategy.
"""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import psycopg
from psycopg.rows import dict_row

from investment.crypto.research.signal_evaluator import CandidateSignalEvaluator
from investment.crypto.research.signal_registry import v29_momentum_signal_registry


def _frame(
    connection: psycopg.Connection[dict[str, object]],
    query: str,
    values: tuple[object, ...],
) -> pl.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(query, values)
        rows = cursor.fetchall()
    return pl.DataFrame(rows, infer_schema_length=None)


def run(database_url: str, experiment_id: str) -> dict[str, object]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        snapshots = _frame(
            connection,
            """
            SELECT snapshot_id, decision_time, market, eligible, score, raw_score,
                   momentum_1h, momentum_4h, momentum_24h, volatility, liquidity,
                   expected_relative_return_1h, expected_relative_return_4h,
                   fee_adjusted_expected_return
              FROM investment.decision_snapshot
             WHERE experiment_id = %s
            """,
            (experiment_id,),
        )
        outcomes = _frame(
            connection,
            """
            SELECT o.snapshot_id, o.horizon_minutes, o.status, o.forward_return,
                   o.mfe, o.mae
              FROM investment.decision_outcome_minute o
              JOIN investment.decision_snapshot s USING (snapshot_id)
             WHERE s.experiment_id = %s
            """,
            (experiment_id,),
        )

    registry = v29_momentum_signal_registry()
    signals = registry.materialize(snapshots)
    research_horizons = {15, 60, 240, 720}
    research_outcomes = outcomes.filter(pl.col("horizon_minutes").is_in(research_horizons))
    evaluations = CandidateSignalEvaluator().evaluate(signals, snapshots, research_outcomes)
    scored = snapshots.filter(pl.col("score").is_not_null())
    return {
        "reportType": "DESCRIPTIVE_IN_SAMPLE_RESEARCH_NOT_OOS_VALIDATION",
        "generatedAt": datetime.now(UTC).isoformat(),
        "experimentId": experiment_id,
        "cohortAudit": {
            "rawSnapshotRows": snapshots.height,
            "scoredRows": scored.height,
            "uniqueDecisionTimes": snapshots.get_column("decision_time").n_unique(),
            "uniqueMarkets": snapshots.get_column("market").n_unique(),
            "scoredMarkets": scored.get_column("market").n_unique(),
        },
        "signalDefinitions": [item.to_dict() for item in registry.definitions()],
        "evaluations": [item.to_dict() for item in evaluations],
        "calibration": _calibration(snapshots, outcomes),
        "warnings": [
            "Adjacent 15-minute cohorts and forward windows overlap.",
            "Episode and non-overlapping block counts are disclosed; row count is not "
            "an effective sample size.",
            "Only one V2.9 production-control experiment is loaded to avoid duplicated cohorts.",
            "No parameter selection or strategy promotion is performed by this report.",
        ],
    }


def _calibration(snapshots: pl.DataFrame, outcomes: pl.DataFrame) -> list[dict[str, object]]:
    rows = []
    mapping = {60: "expected_relative_return_1h", 240: "expected_relative_return_4h"}
    completed = outcomes.filter(pl.col("status") == "COMPLETED")
    for horizon, prediction in mapping.items():
        joined = snapshots.filter(pl.col("score").is_not_null()).select(
            "snapshot_id", "decision_time", prediction
        ).join(
            completed.filter(pl.col("horizon_minutes") == horizon).select(
                "snapshot_id", "forward_return"
            ),
            on="snapshot_id",
            how="inner",
        ).drop_nulls()
        if joined.is_empty():
            continue
        joined = joined.with_columns(
            pl.col("forward_return").median().over("decision_time").alias("cohort_median_return")
        ).with_columns(
            (pl.col("forward_return") - pl.col("cohort_median_return")).alias(
                "realized_relative_return"
            )
        )
        error = joined.get_column(prediction) - joined.get_column("realized_relative_return")
        rows.append(
            {
                "horizonMinutes": horizon,
                "count": joined.height,
                "meanPredictedReturn": joined.get_column(prediction).mean(),
                "meanRealizedRelativeReturn": joined.get_column(
                    "realized_relative_return"
                ).mean(),
                "meanCalibrationError": error.mean(),
                "meanAbsoluteCalibrationError": error.abs().mean(),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    arguments = parser.parse_args()
    if not arguments.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    report = run(arguments.database_url, arguments.experiment_id)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = arguments.output.with_suffix(arguments.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(arguments.output)


if __name__ == "__main__":
    main()
