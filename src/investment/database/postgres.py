"""Small DB-API compatibility layer for PostgreSQL-backed runtime repositories."""

from __future__ import annotations

import re
from importlib.resources import files
from threading import RLock
from typing import Any, Final

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from investment.database.migration import validate_schema_name

_INITIALIZE_LOCK: Final = RLock()
_INITIALIZED: set[tuple[str, str]] = set()
_JULIANDAY = re.compile(r"julianday\(([^)]+)\)", re.IGNORECASE)


def initialize_postgres_schema(dsn: str, schema: str) -> None:
    """Create/upgrade the shared runtime schema once per process."""

    schema = validate_schema_name(schema)
    identity = (dsn, schema)
    with _INITIALIZE_LOCK:
        if identity in _INITIALIZED:
            return
        template = files("investment.database").joinpath("postgres_schema.sql").read_text()
        with psycopg.connect(dsn) as connection:
            connection.execute(template.replace("{schema}", schema))
        _INITIALIZED.add(identity)


def translate_sqlite_statement(statement: str) -> str:
    """Translate the deliberately small SQLite SQL subset used by repositories."""

    translated = statement.strip()
    if translated.upper() == "BEGIN IMMEDIATE":
        return "BEGIN"
    translated = _JULIANDAY.sub(r"\1", translated)
    insert_or_ignore = translated.upper().startswith("INSERT OR IGNORE INTO ")
    if insert_or_ignore:
        translated = "INSERT INTO " + translated[len("INSERT OR IGNORE INTO ") :]
        translated = translated.rstrip(";") + " ON CONFLICT DO NOTHING"
    return translated.replace("?", "%s")


class PostgresConnectionAdapter:
    """Expose the sqlite-style execute/context API used by existing repositories."""

    def __init__(self, dsn: str, schema: str, *, read_only: bool = False) -> None:
        self.schema = validate_schema_name(schema)
        self._connection = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
        self._connection.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema))
        )
        if read_only:
            self._connection.execute("SET default_transaction_read_only = on")
        self._connection.autocommit = False

    def execute(
        self, statement: str, parameters: tuple[object, ...] | list[object] = ()
    ) -> Any:
        return self._connection.execute(
            translate_sqlite_statement(statement),
            tuple(parameters),
        )

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> PostgresConnectionAdapter:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()


def postgres_connection(
    dsn: str, schema: str = "investment", *, read_only: bool = False
) -> PostgresConnectionAdapter:
    return PostgresConnectionAdapter(dsn, schema, read_only=read_only)
