# ADR 0006: Data revision policy

## Context

On-chain and vendor datasets may publish multiple values for the same metric and economic event.
Overwriting the original value would make old experiments irreproducible.

## Decision

Normalized storage preserves `revision` and `valid_from`. For an as-of query, the engine first
removes records not yet available or valid, then selects the latest known revision for each source,
entity, metric, and event-time key. Dataset snapshots hash the resolved research frame.

## Consequences

Past and revised values can coexist in Parquet. A full temporal database is unnecessary for this
phase, but providers must supply stable revision identifiers when their vendor supports revisions.
