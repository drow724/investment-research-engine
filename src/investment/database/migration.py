"""Consistent, restartable SQLite-to-PostgreSQL snapshot migration."""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Final, cast

import psycopg
from psycopg import sql

_SCHEMA_PATTERN: Final = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


@dataclass(frozen=True, slots=True)
class SourceDatabase:
    name: str
    path: Path
    tables: tuple[str, ...]


SOURCES: Final = (
    SourceDatabase(
        "paper",
        Path("data/paper/crypto-trading.sqlite3"),
        (
            "paper_portfolio",
            "paper_position",
            "paper_execution",
            "paper_rebalance_decision",
        ),
    ),
    SourceDatabase(
        "observation",
        Path("data/observations/crypto-forward.sqlite3"),
        (
            "observation_experiment",
            "decision_snapshot",
            "decision_outcome",
            "decision_outcome_minute",
            "decision_market_context",
            "decision_selection_variant",
        ),
    ),
    SourceDatabase(
        "derivatives",
        Path("data/observations/crypto-derivatives.sqlite3"),
        (
            "derivatives_snapshot",
            "mark_price_observation",
            "liquidation_event",
            "coinbase_price_observation",
            "market_stream_status",
            "squeeze_signal",
            "crowding_signal",
        ),
    ),
)

ALL_TABLES: Final = tuple(table for source in SOURCES for table in source.tables)


@dataclass(frozen=True, slots=True)
class MigrationReport:
    started_at: str
    finished_at: str
    schema: str
    source_paths: dict[str, str]
    source_rows: dict[str, int]
    target_rows: dict[str, int]
    quick_checks: dict[str, str]

    @property
    def verified(self) -> bool:
        return self.source_rows == self.target_rows and all(
            result == "ok" for result in self.quick_checks.values()
        )

    def to_json(self) -> str:
        return json.dumps(
            {**asdict(self), "verified": self.verified},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )


def validate_schema_name(value: str) -> str:
    normalized = value.strip()
    if not _SCHEMA_PATTERN.fullmatch(normalized):
        raise ValueError("PostgreSQL schema must be a safe lowercase identifier")
    return normalized


def migrate_sqlite_to_postgres(
    dsn: str,
    *,
    paper_database: str | Path,
    observation_database: str | Path,
    derivatives_database: str | Path,
    schema: str = "investment",
    replace: bool = False,
    batch_size: int = 10_000,
    snapshot_sources: bool = True,
) -> MigrationReport:
    """Copy point-in-time SQLite snapshots into PostgreSQL and verify every row count.

    SQLite's online backup API is used first.  The application may keep running while
    the three independent source snapshots are created, but a final production cutover
    must still stop writers to establish one cross-database cutoff.
    """

    if not dsn.strip():
        raise ValueError("PostgreSQL DSN is required")
    if batch_size <= 0:
        raise ValueError("batch size must be positive")
    schema = validate_schema_name(schema)
    source_paths = {
        "paper": Path(paper_database),
        "observation": Path(observation_database),
        "derivatives": Path(derivatives_database),
    }
    for name, path in source_paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"{name} SQLite database does not exist: {path}")

    started = datetime.now(UTC)
    source_rows: dict[str, int] = {}
    target_rows: dict[str, int] = {}
    quick_checks: dict[str, str] = {}

    if snapshot_sources:
        with tempfile.TemporaryDirectory(prefix="investment-pg-migration-") as temporary:
            snapshot_paths = _snapshot_sources(source_paths, Path(temporary), quick_checks)
            _migrate_snapshots(
                dsn,
                snapshot_paths,
                schema,
                replace,
                batch_size,
                source_rows,
                target_rows,
            )
    else:
        snapshot_paths = dict(source_paths)
        for name, source_path in snapshot_paths.items():
            quick_checks[name] = _quick_check(source_path)
            if quick_checks[name] != "ok":
                raise RuntimeError(f"{name} SQLite database failed quick_check")
        _migrate_snapshots(
            dsn,
            snapshot_paths,
            schema,
            replace,
            batch_size,
            source_rows,
            target_rows,
        )

    finished = datetime.now(UTC)
    return MigrationReport(
        started.isoformat(),
        finished.isoformat(),
        schema,
        {name: str(path) for name, path in source_paths.items()},
        source_rows,
        target_rows,
        quick_checks,
    )


def _snapshot_sources(
    source_paths: dict[str, Path], temporary: Path, quick_checks: dict[str, str]
) -> dict[str, Path]:
    snapshots: dict[str, Path] = {}
    for name, source_path in source_paths.items():
        snapshot = temporary / f"{name}.sqlite3"
        _snapshot_sqlite(source_path, snapshot)
        snapshots[name] = snapshot
        quick_checks[name] = _quick_check(snapshot)
        if quick_checks[name] != "ok":
            raise RuntimeError(f"{name} SQLite snapshot failed quick_check")
    return snapshots


def _migrate_snapshots(
    dsn: str,
    snapshot_paths: dict[str, Path],
    schema: str,
    replace: bool,
    batch_size: int,
    source_rows: dict[str, int],
    target_rows: dict[str, int],
) -> None:
    with psycopg.connect(dsn) as connection:
        _initialize_schema(connection, schema)
        _prepare_target(connection, schema, replace)
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        for source in SOURCES:
            sqlite_path = snapshot_paths[source.name]
            with sqlite3.connect(
                f"{sqlite_path.as_uri()}?mode=ro", uri=True
            ) as sqlite_connection:
                sqlite_connection.row_factory = sqlite3.Row
                for table in source.tables:
                    count = _copy_table(
                        sqlite_connection,
                        connection,
                        table,
                        batch_size=batch_size,
                    )
                    source_rows[table] = count
        for table in ALL_TABLES:
            target_rows[table] = _postgres_count(
                connection,
                table,
            )
        if source_rows != target_rows:
            raise RuntimeError("PostgreSQL row counts do not match SQLite snapshots")


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    with (
        sqlite3.connect(
            f"{source.resolve().as_uri()}?mode=ro", uri=True
        ) as input_connection,
        sqlite3.connect(destination) as output_connection,
    ):
        input_connection.backup(output_connection, pages=4096)


def _quick_check(path: Path) -> str:
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
        row = connection.execute("PRAGMA quick_check").fetchone()
    return "missing" if row is None else str(row[0])


def _initialize_schema(connection: psycopg.Connection[tuple[object, ...]], schema: str) -> None:
    template = files("investment.database").joinpath("postgres_schema.sql").read_text()
    connection.execute(template.replace("{schema}", schema))


def _prepare_target(
    connection: psycopg.Connection[tuple[object, ...]], schema: str, replace: bool
) -> None:
    connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
    populated = {
        table: _postgres_count(connection, table)
        for table in ALL_TABLES
    }
    nonempty = {table: count for table, count in populated.items() if count}
    if nonempty and not replace:
        raise RuntimeError(
            "PostgreSQL migration target is not empty; use --replace for a verified reload"
        )
    if replace:
        identifiers = sql.SQL(", ").join(sql.Identifier(table) for table in reversed(ALL_TABLES))
        connection.execute(sql.SQL("TRUNCATE {} CASCADE").format(identifiers))


def _copy_table(
    source: sqlite3.Connection,
    target: psycopg.Connection[tuple[object, ...]],
    table: str,
    *,
    batch_size: int,
) -> int:
    columns = tuple(
        str(row[1]) for row in source.execute(f'PRAGMA table_info("{table}")').fetchall()
    )
    if not columns:
        raise RuntimeError(f"SQLite source table is missing: {table}")
    cursor = source.execute(f'SELECT * FROM "{table}"')
    copy_statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
    )
    copied = 0
    with target.cursor().copy(copy_statement) as copy:
        while batch := cursor.fetchmany(batch_size):
            for row in batch:
                copy.write_row(tuple(row))
            copied += len(batch)
    return copied


def _postgres_count(
    connection: psycopg.Connection[tuple[object, ...]], table: str
) -> int:
    row = connection.execute(
        sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
    ).fetchone()
    if row is None:
        raise RuntimeError(f"PostgreSQL count query returned no row: {table}")
    return int(cast(int, row[0]))
