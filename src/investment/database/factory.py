"""Storage backend factories used by HTTP and command-line composition roots."""

from __future__ import annotations

from pathlib import Path

from investment.crypto.derivatives.repository import SqliteDerivativesObservationRepository
from investment.crypto.infrastructure.sqlite_accounting import SqlitePaperPortfolioRepository
from investment.crypto.observation.repository import SqliteObservationRepository
from investment.database.repositories import (
    PostgresDerivativesObservationRepository,
    PostgresObservationRepository,
    PostgresPaperPortfolioRepository,
)


def paper_repository(
    database_url: str | None, schema: str, sqlite_path: str | Path
) -> SqlitePaperPortfolioRepository:
    if database_url:
        return PostgresPaperPortfolioRepository(database_url, schema)
    return SqlitePaperPortfolioRepository(sqlite_path)


def observation_repository(
    database_url: str | None, schema: str, sqlite_path: str | Path
) -> SqliteObservationRepository:
    if database_url:
        return PostgresObservationRepository(database_url, schema)
    return SqliteObservationRepository(sqlite_path)


def derivatives_repository(
    database_url: str | None, schema: str, sqlite_path: str | Path
) -> SqliteDerivativesObservationRepository:
    if database_url:
        return PostgresDerivativesObservationRepository(database_url, schema)
    return SqliteDerivativesObservationRepository(sqlite_path)
