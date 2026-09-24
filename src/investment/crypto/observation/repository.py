"""SQLite research store for experiments, immutable decisions, and separate outcomes."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from investment.crypto.observation.domain import (
    DecisionAction,
    DecisionMarketContext,
    DecisionOutcome,
    DecisionSelectionVariant,
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
                    forward_return REAL, mfe REAL, mae REAL, missing_reason TEXT,
                    PRIMARY KEY(snapshot_id, horizon_minutes),
                    FOREIGN KEY(snapshot_id) REFERENCES decision_snapshot(snapshot_id)
                );
                CREATE TABLE IF NOT EXISTS decision_market_context (
                    experiment_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    decision_time TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    PRIMARY KEY(experiment_id, decision_id),
                    FOREIGN KEY(experiment_id) REFERENCES observation_experiment(experiment_id)
                );
                CREATE TABLE IF NOT EXISTS decision_selection_variant (
                    snapshot_id TEXT NOT NULL,
                    variant_id TEXT NOT NULL,
                    selected INTEGER NOT NULL,
                    target_position REAL NOT NULL,
                    reason TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, variant_id),
                    FOREIGN KEY(snapshot_id) REFERENCES decision_snapshot(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_snapshot_experiment_time
                    ON decision_snapshot(experiment_id, decision_time);
                CREATE INDEX IF NOT EXISTS idx_selection_variant_lookup
                    ON decision_selection_variant(variant_id, snapshot_id);
                """
            )
            self._migrate(connection)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(decision_snapshot)")}
        additions = {
            "momentum_1h": "REAL",
            "momentum_4h": "REAL",
            "momentum_24h": "REAL",
            "volatility": "REAL",
            "reference_at": "TEXT",
            "selected_rank": "INTEGER",
            "raw_score": "REAL",
            "score_penalty": "REAL",
            "expected_relative_return_1h": "REAL",
            "expected_relative_return_4h": "REAL",
            "fee_adjusted_expected_return": "REAL",
            "candidate_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
        }
        for name, kind in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE decision_snapshot ADD COLUMN {name} {kind}")
        outcome_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(decision_outcome_minute)")
        }
        if "missing_reason" not in outcome_columns:
            connection.execute("ALTER TABLE decision_outcome_minute ADD COLUMN missing_reason TEXT")
        connection.execute(
            """INSERT OR IGNORE INTO decision_outcome_minute
               (snapshot_id, horizon_minutes, target_at, evaluated_at, status,
                forward_return, mfe, mae)
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

    def missing_reason_counts(self, experiment_id: str) -> dict[str, int]:
        """Separate newly diagnosed gaps from immutable legacy missing outcomes."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT COALESCE(o.missing_reason, 'LEGACY_UNCLASSIFIED') AS reason,
                          COUNT(*) AS occurrences
                   FROM decision_outcome_minute o JOIN decision_snapshot s
                     ON s.snapshot_id=o.snapshot_id
                   WHERE s.experiment_id=? AND o.status='MISSING_DATA'
                   GROUP BY COALESCE(o.missing_reason, 'LEGACY_UNCLASSIFIED')
                   ORDER BY occurrences DESC, reason""",
                (experiment_id,),
            ).fetchall()
        return {str(row["reason"]): int(row["occurrences"]) for row in rows}

    def save_snapshots(self, values: tuple[DecisionSnapshot, ...]) -> int:
        with self._connect() as connection:
            return self._insert_snapshots(connection, values)

    def save_decision_bundle(
        self,
        values: tuple[DecisionSnapshot, ...],
        context: DecisionMarketContext | None,
        selection_variants: tuple[DecisionSelectionVariant, ...] = (),
    ) -> int:
        """Atomically store one frozen cohort, context, and rule-control selections."""

        if context is not None:
            for value in values:
                if (
                    value.experiment_id != context.experiment_id
                    or value.decision_id != context.decision_id
                    or value.strategy_version != context.strategy_version
                    or value.config_hash != context.config_hash
                    or value.decision_time != context.decision_time
                ):
                    raise ValueError("decision snapshots and market context identities must match")
        snapshot_ids = {value.snapshot_id for value in values}
        if any(value.snapshot_id not in snapshot_ids for value in selection_variants):
            raise ValueError("selection variants must reference a snapshot in the same bundle")
        with self._connect() as connection:
            inserted = self._insert_snapshots(connection, values)
            if context is not None:
                self._insert_market_context(connection, context)
            self._insert_selection_variants(connection, selection_variants)
        return inserted

    @staticmethod
    def _insert_snapshots(
        connection: sqlite3.Connection,
        values: tuple[DecisionSnapshot, ...],
    ) -> int:
        inserted = 0
        for value in values:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO decision_snapshot
                   (snapshot_id, experiment_id, decision_id, strategy_version, config_hash,
                    decision_time, asset, market, action, reason, score, rank, eligible,
                    selected, current_position, target_position, portfolio_cash,
                    portfolio_equity, current_exposure, target_exposure, reference_price,
                    liquidity, hour_of_day, day_of_week, momentum_1h, momentum_4h,
                    momentum_24h, volatility, reference_at, selected_rank, raw_score,
                    score_penalty, expected_relative_return_1h,
                    expected_relative_return_4h, fee_adjusted_expected_return,
                    candidate_reasons_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    value.raw_score,
                    value.score_penalty,
                    value.expected_relative_return_1h,
                    value.expected_relative_return_4h,
                    value.fee_adjusted_expected_return,
                    value.candidate_reasons_json,
                ),
            )
            inserted += cursor.rowcount
        return inserted

    @staticmethod
    def _insert_selection_variants(
        connection: sqlite3.Connection,
        values: tuple[DecisionSelectionVariant, ...],
    ) -> None:
        for value in values:
            connection.execute(
                """INSERT OR IGNORE INTO decision_selection_variant
                   (snapshot_id, variant_id, selected, target_position, reason)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    value.snapshot_id,
                    value.variant_id,
                    int(value.selected),
                    value.target_position,
                    value.reason,
                ),
            )

    def save_market_context(self, value: DecisionMarketContext) -> bool:
        with self._connect() as connection:
            return self._insert_market_context(connection, value)

    @staticmethod
    def _insert_market_context(
        connection: sqlite3.Connection,
        value: DecisionMarketContext,
    ) -> bool:
        cursor = connection.execute(
            """INSERT OR IGNORE INTO decision_market_context
               (experiment_id, decision_id, strategy_version, config_hash,
                decision_time, context_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                value.experiment_id,
                value.decision_id,
                value.strategy_version,
                value.config_hash,
                value.decision_time.isoformat(),
                value.context_json,
            ),
        )
        return cursor.rowcount == 1

    def market_contexts(self, experiment_id: str) -> tuple[DecisionMarketContext, ...]:
        self.experiment(experiment_id)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM decision_market_context WHERE experiment_id=?
                   ORDER BY decision_time""",
                (experiment_id,),
            ).fetchall()
        return tuple(
            DecisionMarketContext(
                str(row["experiment_id"]),
                str(row["decision_id"]),
                str(row["strategy_version"]),
                str(row["config_hash"]),
                datetime.fromisoformat(str(row["decision_time"])),
                str(row["context_json"]),
            )
            for row in rows
        )

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

    def selection_variants(
        self,
        experiment_id: str,
        variant_id: str | None = None,
    ) -> tuple[DecisionSelectionVariant, ...]:
        """Read counterfactual selections belonging to one immutable experiment lane."""
        clauses = ["s.experiment_id=?"]
        parameters: list[str] = [experiment_id]
        if variant_id is not None:
            clauses.append("v.variant_id=?")
            parameters.append(variant_id)
        where = " AND ".join(clauses)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT v.* FROM decision_selection_variant v
                    JOIN decision_snapshot s ON s.snapshot_id=v.snapshot_id
                    WHERE {where}
                    ORDER BY s.decision_time, s.decision_id, s.market, v.variant_id""",
                tuple(parameters),
            ).fetchall()
        return tuple(self._selection_variant(row) for row in rows)

    def save_outcome(self, value: DecisionOutcome) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO decision_outcome_minute
                   (snapshot_id, horizon_minutes, target_at, evaluated_at, status,
                    forward_return, mfe, mae, missing_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    value.snapshot_id,
                    value.horizon_minutes,
                    value.target_at.isoformat(),
                    value.evaluated_at.isoformat(),
                    value.status.value,
                    value.forward_return,
                    value.mfe,
                    value.mae,
                    value.missing_reason,
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
            row["raw_score"],
            row["score_penalty"],
            row["expected_relative_return_1h"],
            row["expected_relative_return_4h"],
            row["fee_adjusted_expected_return"],
            str(row["candidate_reasons_json"]),
        )

    @staticmethod
    def _selection_variant(row: sqlite3.Row) -> DecisionSelectionVariant:
        return DecisionSelectionVariant(
            str(row["snapshot_id"]),
            str(row["variant_id"]),
            bool(row["selected"]),
            float(row["target_position"]),
            str(row["reason"]),
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
            row["missing_reason"],
        )
