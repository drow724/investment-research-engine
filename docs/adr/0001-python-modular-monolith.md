# ADR 0001: Python modular monolith

## Context

The research engine will eventually support Bitcoin, stocks, gold, and funds. Splitting these
domains into services now would add operational complexity before their boundaries are proven.

## Decision

Use one Python repository with explicit `core`, `application`, asset-domain, and `interfaces`
boundaries. Only abstractions proven asset-independent belong in `core`.

## Consequences

Domains can share deployment and tooling while remaining separable. Boundary discipline must be
maintained in code review; services may be extracted later when operational needs justify it.
