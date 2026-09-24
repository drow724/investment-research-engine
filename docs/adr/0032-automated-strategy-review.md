# ADR 0032: Deterministic Strategy Review and Prepared Paper Promotion

## Status

Accepted for Phase 1.

## Context

Dynamic crypto strategies need recurring evidence reviews, but an LLM narrative or a short run of
Paper profit must not directly change the executing version. The active FastAPI lifespan currently
freezes one strategy policy, portfolio, and observation experiment until restart. Reusing any of
those identities can mix confirmation state, trades, or forward outcomes across versions.

## Decision

Add a separate strategy-review boundary with four operations:

1. `analyze` reads only already-materialized SQLite evidence at one common completed-decision
   cutoff and writes an immutable report.
2. `propose` accepts only a mature `REJECTED` report and applies an explicit, typed, allowlisted
   patch over a complete frozen parent TOML policy.
3. `validate` reloads every source and verifies the parent, candidate, experiment, analysis, patch,
   and artifact hashes.
4. `promote-paper` accepts only a candidate `PROMOTION_READY` report and writes a prepared
   next-restart Paper manifest after proving the proposed portfolio and experiment IDs are unused.

The review gate policy uses unique 15-minute decision cohorts. It requires at least three Seoul
calendar dates, 192 mature 1-hour cohorts, 96 mature 4-hour cohorts, 98% decision coverage, at most
1% missing scored/selected outcomes, cost-aware selected return and matched spread, positive
cross-sectional score IC, temporal stability, positive Paper net return, profit factor of at least
1.2, drawdown no greater than 2%, and single-asset cohort concentration no greater than 25%.

Automatic candidates remain within a conservative Paper safety envelope: no more than three
positions, 90% invested, 40% per asset, 6x base turnover, 8x bullish turnover, 0.5% daily fee
budget, 2% daily realized-loss budget, and at least 50% sell turnover weight.

## Safety properties

- Analysis connections use SQLite URI `mode=ro`, `query_only=ON`, and a busy timeout.
- The analyzer does not call Upbit, Parquet providers, outcome evaluation, or execution services.
- Policy Decimal values are quoted, durations use whole seconds, and every field is present or
  explicitly null.
- Immutable retries are idempotent; a reused identity with different content fails.
- Candidate and promotion provenance includes source identities, cutoffs, gate results, and hashes.
- Promotion preparation never edits runtime configuration or current V2.3 state.
- Only Paper activation is representable; no live exchange gateway is introduced.
- Superseded observations must remain in the outcome drain set through their 24-hour horizon plus
  grace.

## Consequences

Daily analysis and candidate preparation can be automated without granting the analysis process
trading authority. A later phase must add an explicit restart/control-plane activator and a way to
run champion and challenger policies concurrently before a prepared manifest can be applied.
