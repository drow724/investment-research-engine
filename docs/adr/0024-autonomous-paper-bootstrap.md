# ADR 0024: Autonomous local Paper bootstrap

## Status

Accepted

## Decision

When `INVESTMENT_RUNTIME_DYNAMIC_PAPER_PORTFOLIO_ID` is configured, application startup
idempotently creates that portfolio if it does not exist and registers the hourly dynamic Paper
rebalance. Initial virtual KRW is configurable and is applied only at creation. Existing balances,
positions, and executions are never reset at startup.

Execution remains separately controlled by `INVESTMENT_RUNTIME_DYNAMIC_PAPER_EXECUTE`. This local
checkout enables it in a Git-ignored `.env` for `paper-main`. The committed `.env.example` keeps
execution false so another checkout cannot enable Paper fills merely by copying source code.

## Consequences

Running `python -m investment` is sufficient for this user's local autonomous Paper loop. The
hourly job uses the point-in-time universe, stored completed candles, deterministic risk checks,
and SQLite Paper accounting. This decision grants no live-trading capability: there is no private
Upbit client, credential handling, or live exchange gateway.
