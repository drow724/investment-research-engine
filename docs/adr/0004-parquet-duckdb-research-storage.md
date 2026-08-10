# ADR 0004: Parquet and DuckDB research storage

## Context

Market observations and derived datasets are analytical data and should not live in the future
platform database.

## Decision

Persist original provider payloads as deterministic JSON and normalized observations as Parquet.
Use Polars for transformations and support DuckDB-compatible Parquet querying.

## Consequences

Storage stays local, reproducible, and inexpensive. Concurrent transactional workflows remain the
responsibility of the future control plane rather than this research store.
