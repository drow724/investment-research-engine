# ADR 0031: Adaptive-turnover V2.3 Paper challenger

## Status

Accepted for isolated Paper execution on 2026-08-21. It is not approved for live trading.

## Context

V2.2 used a fixed daily turnover ceiling of two times equity. On 2026-08-21 it exhausted the
ceiling after four round trips and then blocked 32 decision cycles. Selected candidates after the
block returned 0.49% at one hour and 2.09% at four hours on average, while the same scored but
unselected pool returned 0.21% and 0.92%. The observation is consistent with a real opportunity
cost in a broad rally, but it is one regime and the repeated decisions are not independent trades.

Removing the ceiling would also remove a useful circuit breaker against churn. V2.3 therefore
changes only the turnover budget and preserves V2.2 selection, position, fee, and realized-loss
rules.

## Decision

Publish `dynamic-intraday-v2.3` as a separately fingerprinted Paper strategy.

- Keep the base daily turnover ceiling at 2x equity.
- Expand it to 3x only when all of the following are true:
  - BTC four-hour momentum is at least +1%;
  - BTC 24-hour momentum is at least +3%;
  - at least 65% of the 20 most-liquid scored markets have positive four-hour and 24-hour momentum;
  - at least ten scored markets are available.
- Charge buys at 100% and sells at 50% against the turnover-entry budget. Actual fees still count
  at 100% against the unchanged 0.3% daily fee ceiling.
- Preserve the unchanged 1% daily realized-loss limit.
- Persist the active base/bullish regime and effective 2x/3x limit in decision reasons.

The 50% sell weight recycles part of capital released by exits without pretending that sells are
free: their full exchange fee remains in the independent fee circuit breaker.

## Isolation and evaluation

V2.3 uses a new portfolio and observation experiment. V2.2 is interrupted with a supersession
reason and its outstanding outcomes continue through the drain evaluator. Compare V2.3 with V2.2
on net return, total fees, turnover, blocked cycles, drawdown, and one-/four-hour selected-candidate
forward returns. Do not promote it to live execution from a single bullish episode.
