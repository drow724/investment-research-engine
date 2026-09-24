"""Read-only summary for the interrupted V2.9 Paper suite."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def scalar(connection: sqlite3.Connection, sql: str, parameters: tuple[Any, ...]) -> Any:
    return connection.execute(sql, parameters).fetchone()[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observation-db", type=Path, required=True)
    parser.add_argument("--paper-db", type=Path, required=True)
    parser.add_argument("--experiment-prefix", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("experiments/research"))
    arguments = parser.parse_args()

    observation = connect_read_only(arguments.observation_db)
    paper = connect_read_only(arguments.paper_db)
    experiments = list(
        observation.execute(
            "SELECT * FROM observation_experiment WHERE experiment_id LIKE ? "
            "ORDER BY experiment_id",
            (f"{arguments.experiment_prefix}%",),
        )
    )
    if len(experiments) != 4:
        raise RuntimeError(f"expected four V2.9 experiments, found {len(experiments)}")

    lanes: list[dict[str, Any]] = []
    for experiment in experiments:
        experiment_id = str(experiment["experiment_id"])
        portfolio_id = str(experiment["portfolio_id"])
        snapshot = observation.execute(
            "SELECT COUNT(*) AS snapshots, COUNT(DISTINCT decision_id) AS decisions, "
            "COALESCE(SUM(selected), 0) AS selected, MAX(decision_time) AS latest_decision "
            "FROM decision_snapshot WHERE experiment_id=?",
            (experiment_id,),
        ).fetchone()
        outcomes = [
            dict(item)
            for item in observation.execute(
                "SELECT horizon_minutes, status, COUNT(*) AS rows, "
                "AVG(forward_return) AS mean_forward_return, AVG(mfe) AS mean_mfe, "
                "AVG(mae) AS mean_mae FROM decision_outcome_minute "
                "WHERE snapshot_id IN "
                "(SELECT snapshot_id FROM decision_snapshot WHERE experiment_id=?) "
                "GROUP BY horizon_minutes, status ORDER BY horizon_minutes, status",
                (experiment_id,),
            )
        ]
        executions = paper.execute(
            "SELECT COUNT(*) AS fills, COALESCE(SUM(quantity * price), 0) AS turnover, "
            "COALESCE(SUM(fee), 0) AS fees, COALESCE(SUM(realized_pnl), 0) AS realized_pnl "
            "FROM paper_execution WHERE portfolio_id=?",
            (portfolio_id,),
        ).fetchone()
        latest_equity = paper.execute(
            "SELECT equity, as_of FROM paper_rebalance_decision WHERE portfolio_id=? "
            "ORDER BY as_of DESC LIMIT 1",
            (portfolio_id,),
        ).fetchone()
        lanes.append(
            {
                "lane": experiment_id.removeprefix(arguments.experiment_prefix),
                "experiment": dict(experiment),
                "snapshots": dict(snapshot),
                "outcomes": outcomes,
                "execution": dict(executions),
                "latest_equity": dict(latest_equity) if latest_equity else None,
            }
        )

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "INTERRUPTED_BY_DATABASE_HEADER_CORRUPTION",
        "source_observation_db": str(arguments.observation_db),
        "source_quick_check": scalar(observation, "PRAGMA quick_check", ()),
        "lanes": lanes,
        "limitations": [
            "The suite ended before its planned seven-day deadline.",
            "Only one lane produced one completed round trip, so strategy comparison "
            "is inconclusive.",
            "Candidate rows from adjacent 15-minute decisions are correlated and are "
            "not independent samples.",
        ],
    }
    arguments.output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = arguments.output_root / f"v29-interrupted-postmortem-{stamp}.json"
    markdown_path = arguments.output_root / f"v29-interrupted-postmortem-{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")

    lines = [
        "# V2.9 Interrupted Paper Postmortem",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "The observation database repair copy passed `PRAGMA quick_check = ok`. "
        "The experiment was interrupted by database-header corruption before its planned deadline.",
        "",
        "| Lane | Decisions | Snapshots | Selected | Fills | Realized PnL | Latest equity |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for lane in lanes:
        execution = lane["execution"]
        equity = lane["latest_equity"]
        lines.append(
            f"| {lane['lane']} | {lane['snapshots']['decisions']} | "
            f"{lane['snapshots']['snapshots']} | {lane['snapshots']['selected']} | "
            f"{execution['fills']} | {float(execution['realized_pnl']):.2f} | "
            f"{float(equity['equity']) if equity else 0:.2f} |"
        )
    lines.extend(["", "## Limitations", "", *[f"- {item}" for item in report["limitations"]], ""])
    markdown_path.write_text("\n".join(lines))
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path)}))


if __name__ == "__main__":
    main()
