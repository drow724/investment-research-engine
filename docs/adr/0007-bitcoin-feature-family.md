# ADR 0007: Bitcoin feature families

## Context

Raw vendor metrics, statistical transformations, and investment interpretations have different
responsibilities and confidence levels.

## Decision

Separate raw metrics, normalized feature families, and versioned composite research features.
ETF, holder, derivatives, pressure, and divergence transformations consume vendor-neutral frames.
Demand and supply pressure start as unoptimized equal-weight baselines; absorption is demand minus
supply. Missing inputs are not silently treated as zero.

## Consequences

Hypotheses remain falsifiable and vendor adapters can change without changing composites. Linear
weights are intentionally simple and must earn confidence through predictive and stability tests.
