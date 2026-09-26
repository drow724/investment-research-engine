"""PostgreSQL runtime adapters reusing the tested repository domain mapping."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from investment.crypto.derivatives.repository import SqliteDerivativesObservationRepository
from investment.crypto.infrastructure.sqlite_accounting import SqlitePaperPortfolioRepository
from investment.crypto.observation.repository import SqliteObservationRepository
from investment.database.postgres import (
    initialize_postgres_schema,
    postgres_connection,
)


class _PostgresRepository:
    dsn: str
    schema: str

    def _configure_postgres(self, dsn: str, schema: str) -> None:
        self.dsn = dsn
        self.schema = schema
        initialize_postgres_schema(dsn, schema)

    def _connect(self) -> Any:
        return postgres_connection(self.dsn, self.schema)


class PostgresPaperPortfolioRepository(
    _PostgresRepository, SqlitePaperPortfolioRepository
):
    def __init__(self, dsn: str, schema: str = "investment") -> None:
        self._configure_postgres(dsn, schema)


class PostgresObservationRepository(_PostgresRepository, SqliteObservationRepository):
    def __init__(self, dsn: str, schema: str = "investment") -> None:
        self._configure_postgres(dsn, schema)


class PostgresDerivativesObservationRepository(
    _PostgresRepository, SqliteDerivativesObservationRepository
):
    def __init__(self, dsn: str, schema: str = "investment") -> None:
        self._configure_postgres(dsn, schema)
        # DerivativesMarketStreams uses this only to derive its process-owner
        # lock file. PostgreSQL data never lives at this path.
        self.path = Path("runtime/state/postgres-derivatives.sqlite3")
