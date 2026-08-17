"""SQLite research store for experiments, immutable decisions, and separate outcomes."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from investment.crypto.observation.domain import (
    DecisionAction,
    DecisionOutcome,
    DecisionSnapshot,
    ObservationExperiment,
    ObservationStatus,
    OutcomeStatus,
)


class SqliteObservationRepository:
    def __init__(self, path: str | Path = "data/observations/crypto-forward.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS observation_experiment (
                    experiment_id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL, config_hash TEXT NOT NULL,
                    started_at TEXT NOT NULL, planned_end_at TEXT NOT NULL,
                    status TEXT NOT NULL, starting_equity REAL NOT NULL,
                    completed_at TEXT, interruption_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS decision_snapshot (
                    snapshot_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
                    config_hash TEXT NOT NULL, decision_time TEXT NOT NULL,
                    asset TEXT NOT NULL, market TEXT NOT NULL, action TEXT NOT NULL,
                    reason TEXT NOT NULL, score REAL, rank INTEGER, eligible INTEGER NOT NULL,
                    selected INTEGER NOT NULL, current_position REAL NOT NULL,
                    target_position REAL NOT NULL, portfolio_cash REAL NOT NULL,
                    portfolio_equity REAL NOT NULL, current_exposure REAL NOT NULL,
                    target_exposure REAL NOT NULL, reference_price REAL, liquidity REAL,
                    hour_of_day INTEGER NOT NULL, day_of_week INTEGER NOT NULL,
                    UNIQUE(experiment_id, decision_id, asset),
                    FOREIGN KEY(experiment_id) REFERENCES observation_experiment(experiment_id)
                );
                CREATE TABLE IF NOT EXISTS decision_outcome (
                    snapshot_id TEXT NOT NULL, horizon_hours INTEGER NOT NULL,
                    target_at TEXT NOT NULL, evaluated_at TEXT NOT NULL, status TEXT NOT NULL,
                    forward_return REAL, mfe REAL, mae REAL,
                    PRIMARY KEY(snapshot_id, horizon_hours),
                    FOREIGN KEY(snapshot_id) REFERENCES decision_snapshot(snapshot_id)
                );
                CREATE TABLE IF NOT EXISTS decision_outcome_minute (
                    snapshot_id TEXT NOT NULL, horizon_minutes INTEGER NOT NULL,
                    target_at TEXT NOT NULL, evaluated_at TEXT NOT NULL, status TEXT NOT NULL,
                    forward_return REAL, mfe REAL, mae REAL,
                    PRIMARY KEY(snapshot_id, horizon_minutes),
                    FOREIGN KEY(snapshot_id) REFERENCES decision_snapshot(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_snapshot_experiment_time
                    ON decision_snapshot(experiment_id, decision_time);
                """
            )
            self._migrate(connection)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(decision_snapshot)")
        }
        additions = {
            "momentum_1h": "REAL",
            "momentum_4h": "REAL",
            "momentum_24h": "REAL",
            "volatility": "REAL",
            "reference_at": "TEXT",
            "selected_rank": "INTEGER",
        }
        for name, kind in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE decision_snapshot ADD COLUMN {name} {kind}")
        connection.execute(
            """INSERT OR IGNORE INTO decision_outcome_minute
               SELECT snapshot_id, horizon_hours * 60, target_at, evaluated_at, status,
                      forward_return, mfe, mae
               FROM decision_outcome"""
        )

    def save_experiment(self, value: ObservationExperiment) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO observation_experiment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(experiment_id) DO UPDATE SET status=excluded.status,
                     completed_at=excluded.completed_at,
                     interruption_reason=excluded.interruption_reason""",
                (
                    value.experiment_id,
                    value.portfolio_id,
                    value.strategy_version,
                    value.config_hash,
                    value.started_at.isoformat(),
                    value.planned_end_at.isoformat(),
                    value.status.value,
                    value.starting_equity,
                    value.completed_at.isoformat() if value.completed_at else None,
                    value.interruption_reason,
                ),
            )

    def experiment(self, experiment_id: str) -> ObservationExperiment:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM observation_experiment WHERE experiment_id=?", (experiment_id,)
            ).fetchone()
        if row is None:
            raise KeyError(experiment_id)
        return self._experiment(row)

    def current(self) -> ObservationExperiment | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM observation_experiment
                   WHERE status IN ('CREATED','RUNNING') ORDER BY started_at DESC LIMIT 1"""
            ).fetchone()
        return self._experiment(row) if row else None

    def experiments(self) -> tuple[ObservationExperiment, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM observation_experiment ORDER BY started_at DESC"
            ).fetchall()
        return tuple(self._experiment(row) for row in rows)

    def health_counts(self, experiment_id: str, current: datetime) -> dict[str, int]:
        cutoffs = tuple(
            (current - timedelta(minutes=minutes)).isoformat()
            for minutes in (15, 30, 60, 240, 720, 1440)
        )
        with self._connect() as connection:
            snapshot = connection.execute(
                """SELECT COUNT(*) AS snapshots,
                          COUNT(DISTINCT decision_time) AS cycles,
                          SUM(CASE WHEN reference_price IS NOT NULL
                               AND julianday(decision_time) <= julianday(?) THEN 1 ELSE 0 END)
                        + SUM(CASE WHEN reference_price IS NOT NULL
                               AND julianday(decision_time) <= julianday(?) THEN 1 ELSE 0 END)
                        + SUM(CASE WHEN reference_price IS NOT NULL
                               AND julianday(decision_time) <= julianday(?) THEN 1 ELSE 0 END)
                        + SUM(CASE WHEN reference_price IS NOT NULL
                               AND julianday(decision_time) <= julianday(?) THEN 1 ELSE 0 END)
                        + SUM(CASE WHEN reference_price IS NOT NULL
                               AND julianday(decision_time) <= julianday(?) THEN 1 ELSE 0 END)
                        + SUM(CASE WHEN reference_price IS NOT NULL
                               AND julianday(decision_time) <= julianday(?) THEN 1 ELSE 0 END)
                            AS matured
                   FROM decision_snapshot WHERE experiment_id=?""",
                (*cutoffs, experiment_id),
            ).fetchone()
            outcome = connection.execute(
                """SELECT COUNT(*) AS outcomes,
                          SUM(CASE WHEN o.status='MISSING_DATA' THEN 1 ELSE 0 END) AS missing
                   FROM decision_outcome_minute o JOIN decision_snapshot s
                     ON s.snapshot_id=o.snapshot_id WHERE s.experiment_id=?""",
                (experiment_id,),
            ).fetchone()
            unresolved = connection.execute(
                """SELECT COUNT(*) AS unresolved FROM decision_snapshot s
                   WHERE s.experiment_id=? AND s.reference_price IS NOT NULL
                     AND julianday(s.decision_time) <= julianday(?)
                     AND NOT EXISTS (
                         SELECT 1 FROM decision_outcome_minute o
                         WHERE o.snapshot_id=s.snapshot_id AND o.horizon_minutes=1440
                     )""",
                (experiment_id, cutoffs[-1]),
            ).fetchone()
        return {
            "actualDecisionCycles": int(snapshot["cycles"] or 0),
            "candidateSnapshots": int(snapshot["snapshots"] or 0),
            "maturedOutcomes": int(snapshot["matured"] or 0),
            "outcomeRows": int(outcome["outcomes"] or 0),
            "missingDataOutcomes": int(outcome["missing"] or 0),
            "unresolvedDecisions": int(unresolved["unresolved"] or 0),
        }

    def save_snapshots(self, values: tuple[DecisionSnapshot, ...]) -> int:
        inserted = 0
        with self._connect() as connection:
            for value in values:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO decision_snapshot
                       (snapshot_id, experiment_id, decision_id, strategy_version, config_hash,
                        decision_time, asset, market, action, reason, score, rank, eligible,
                        selected, current_position, target_position, portfolio_cash,
                        portfolio_equity, current_exposure, target_exposure, reference_price,
                        liquidity, hour_of_day, day_of_week, momentum_1h, momentum_4h,
                        momentum_24h, volatility, reference_at, selected_rank)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        value.snapshot_id,
                        value.experiment_id,
                        value.decision_id,
                        value.strategy_version,
                        value.config_hash,
                        value.decision_time.isoformat(),
                        value.asset,
                        value.market,
                        value.action.value,
                        value.reason,
                        value.score,
                        value.rank,
                        int(value.eligible),
                        int(value.selected),
                        value.current_position,
                        value.target_position,
                        value.portfolio_cash,
                        value.portfolio_equity,
                        value.current_exposure,
                        value.target_exposure,
                        value.reference_price,
                        value.liquidity,
                        value.hour_of_day,
                        value.day_of_week,
                        value.momentum_1h,
                        value.momentum_4h,
                        value.momentum_24h,
                        value.volatility,
                        value.reference_at.isoformat() if value.reference_at else None,
                        value.selected_rank,
                    ),
                )
                inserted += cursor.rowcount
        return inserted

    def snapshots(self, experiment_id: str) -> tuple[DecisionSnapshot, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM decision_snapshot WHERE experiment_id=? "
                "ORDER BY decision_time, rank",
                (experiment_id,),
            ).fetchall()
        return tuple(self._snapshot(row) for row in rows)

    def latest_snapshots(self, experiment_id: str) -> tuple[DecisionSnapshot, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM decision_snapshot
                   WHERE experiment_id=? AND decision_time=(
                       SELECT MAX(decision_time) FROM decision_snapshot WHERE experiment_id=?
                   ) ORDER BY eligible DESC, rank, market""",
                (experiment_id, experiment_id),
            ).fetchall()
        return tuple(self._snapshot(row) for row in rows)

    def save_outcome(self, value: DecisionOutcome) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO decision_outcome_minute
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    value.snapshot_id,
                    value.horizon_minutes,
                    value.target_at.isoformat(),
                    value.evaluated_at.isoformat(),
                    value.status.value,
                    value.forward_return,
                    value.mfe,
                    value.mae,
                ),
            )
        return cursor.rowcount == 1

    def outcomes(self, experiment_id: str) -> tuple[DecisionOutcome, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT o.* FROM decision_outcome_minute o JOIN decision_snapshot s
                   ON s.snapshot_id=o.snapshot_id WHERE s.experiment_id=?
                   ORDER BY s.decision_time, o.horizon_minutes""",
                (experiment_id,),
            ).fetchall()
        return tuple(self._outcome(row) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _experiment(row: sqlite3.Row) -> ObservationExperiment:
        return ObservationExperiment(
            row["experiment_id"],
            row["portfolio_id"],
            row["strategy_version"],
            row["config_hash"],
            datetime.fromisoformat(row["started_at"]),
            datetime.fromisoformat(row["planned_end_at"]),
            ObservationStatus(row["status"]),
            float(row["starting_equity"]),
            datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            row["interruption_reason"],
        )

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> DecisionSnapshot:
        return DecisionSnapshot(
            row["snapshot_id"],
            row["experiment_id"],
            row["decision_id"],
            row["strategy_version"],
            row["config_hash"],
            datetime.fromisoformat(row["decision_time"]),
            row["asset"],
            row["market"],
            DecisionAction(row["action"]),
            row["reason"],
            row["score"],
            row["rank"],
            bool(row["eligible"]),
            bool(row["selected"]),
            row["current_position"],
            row["target_position"],
            row["portfolio_cash"],
            row["portfolio_equity"],
            row["current_exposure"],
            row["target_exposure"],
            row["reference_price"],
            row["liquidity"],
            row["hour_of_day"],
            row["day_of_week"],
            row["momentum_1h"],
            row["momentum_4h"],
            row["momentum_24h"],
            row["volatility"],
            datetime.fromisoformat(row["reference_at"]) if row["reference_at"] else None,
            row["selected_rank"],
        )

    @staticmethod
    def _outcome(row: sqlite3.Row) -> DecisionOutcome:
        return DecisionOutcome(
            row["snapshot_id"],
            row["horizon_minutes"],
            datetime.fromisoformat(row["target_at"]),
            datetime.fromisoformat(row["evaluated_at"]),
            OutcomeStatus(row["status"]),
            row["forward_return"],
            row["mfe"],
            row["mae"],
        )
