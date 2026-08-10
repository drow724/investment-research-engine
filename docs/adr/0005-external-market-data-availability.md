# ADR 0005: External market-data availability

## Context

ETF, on-chain, and derivatives observations can describe an earlier event than the time at which a
researcher could actually see the value. Treating the event timestamp as publication time creates
look-ahead bias.

## Decision

Every normalized metric carries UTC-aware `event_time`, `available_at`, and `ingested_at`.
Provider-specific `DataAvailabilityPolicy` implementations resolve publication time explicitly.
Daily feature rows align external metrics using `available_at`, and point-in-time queries require
`available_at <= as_of`.

## Consequences

Vendor publication metadata or a documented deterministic delay is mandatory. Records with an
unknown publication time are rejected rather than guessed. Some economic-date observations appear
in research on a later day.
