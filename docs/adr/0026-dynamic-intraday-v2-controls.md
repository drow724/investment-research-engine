# ADR 0026: Dynamic intraday v2 controls and decision audit

## Status

Accepted

## Decision

The 15-minute evaluation cadence remains, but entry and exit are no longer symmetric. A new asset
must exceed a 0.5% score hurdle in two consecutive decisions. A held asset remains eligible while
its score is above -0.2% and its liquid-candidate rank is at most eight. This hysteresis prevents a
single rank change from causing a complete portfolio rotation.

UTC-day risk budgets cap turnover at six times equity, fees at 0.5% of equity, and realized loss at
2% of equity. Exhausting any budget blocks buys while preserving sells. These are conservative
bootstrap values to be validated, not optimized profit parameters.

Full liquidation uses the repository's exact position quantity instead of deriving quantity by
notional division. Quantities below `1e-18` are treated as dust and removed.

Every completed dry-run or execution persists an idempotent decision record containing the
strategy version, as-of timestamp, universe timestamp, equity, all candidate assessments,
selections, planned orders, risk violations, and result status.

## Consequences

The engine still reacts every 15 minutes but should rotate less often, stop adding risk after a bad
day, and provide enough data to calibrate score buckets against future returns. Existing execution
history predates this audit and cannot be retrospectively assigned complete decision features.
Live orders remain unavailable.

## v2.1 amendment

The liquid intraday collection cohort expands from 20 to 50 KRW markets. New or underfilled files
receive a ten-day bootstrap until at least seven days of 15-minute observations exist. Assessment
also uses the wider ten-day window so intermittent markets can satisfy the observation-count rule.

After a sell, the same pair is ineligible for entry for one hour. When all position slots are
occupied by retained assets, a challenger replaces the weakest retained asset only if its score is
at least 1 percentage point higher. Empty slots and assets that independently fail exit hysteresis
do not require this replacement margin.
