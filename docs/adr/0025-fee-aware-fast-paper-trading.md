# ADR 0025: Fee-aware 15-minute Paper evaluation

## Status

Accepted

## Decision

Dynamic Paper selection is evaluated after every completed 15-minute candle at minutes 02, 17, 32,
and 47. The score combines one-hour momentum (45%), four-hour momentum (35%), and 24-hour momentum
(20%), with a short-return volatility penalty. A candidate must exceed a 0.20% round-trip cost
hurdle before selection.

The hurdle consists of the documented KRW fee of 0.05% on each side and a conservative estimated
slippage allowance of 0.05% on each side. The existing KRW 5,000 minimum order, 2% equity rebalance
threshold, 90% invested cap, 40% per-asset cap, maximum of three positions, warning-market filter,
and sell-before-buy ordering remain in force.

Every Paper fill remains in SQLite and is exposed as a read-only execution-history API with fee and
realized-PnL fields. The development dashboard displays the latest 100 fills and cumulative values.

## Consequences

The engine reacts faster while explicitly rejecting signals too small to clear modeled costs. It
does not guarantee profit: candle-level Paper fills omit order-book spread, market impact, partial
fills, latency, and changing fee promotions. No live Upbit order adapter is enabled by this change.
