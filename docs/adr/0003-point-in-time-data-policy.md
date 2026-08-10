# ADR 0003: Point-in-time data policy

## Context

Using values before market participants could know them invalidates financial research.

## Decision

Store distinct UTC-aware `event_time`, `available_at`, and `ingested_at` timestamps. A dataset as
of time `t` exposes only rows with `available_at <= t`. Features consume this filtered dataset;
forward labels are generated through a separate pipeline.

## Consequences

Research is reproducible and resistant to look-ahead bias, at the cost of additional timestamp
metadata and explicit as-of selection.
