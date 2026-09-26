# ADR 0040: PostgreSQL for concurrent runtime and research access

## Status

Accepted and cut over on 2026-09-24. SQLite is retained as an immutable pre-cutover archive;
PostgreSQL is the runtime source of truth when `INVESTMENT_DATABASE_URL` is configured.

## Context

Docker Desktop writes the active SQLite databases inside its VM. Host-side readers and agents
cannot reliably participate in the same WAL locking domain. A damaged observation database has
already demonstrated that an operational rule alone is not a sufficient long-term boundary for
multiple agents, notebooks, dashboards, and a future read-only MCP service.

## Decision

- Add PostgreSQL 17 to Compose with a dedicated Git-ignored data directory and loopback-only
  host port.
- Preserve current textual timestamps and exact decimal strings in the first schema. Semantic
  type improvements are a later, separately validated migration.
- Create consistent per-file snapshots with SQLite's online backup API before copying.
- Copy all Paper, observation, and derivatives tables in one PostgreSQL transaction.
- Refuse to load a non-empty target unless the operator explicitly selects `--replace`.
- Verify every source and target row count and SQLite `PRAGMA quick_check` result.
- Switch Paper, observation, derivatives, and strategy-review access together after repository
  parity tests and a stopped-writer final migration.
- Give future analyst and MCP users separate read-only PostgreSQL roles; never expose arbitrary
  write SQL to an MCP tool.

## Consequences

The runtime never dual-writes. A blank `INVESTMENT_DATABASE_URL` remains an explicit rollback
path to archived SQLite, while normal operation writes only PostgreSQL. Final migration reported
matching counts for every table and new PostgreSQL experiment identities isolate post-cutover
evidence from historical SQLite-era runs.
