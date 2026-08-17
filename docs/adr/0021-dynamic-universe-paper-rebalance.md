# ADR 0021: Dynamic universe selection for Paper rebalancing

## Status

Accepted

## Decision

Paper rebalancing starts from the point-in-time KRW trading-universe snapshot, not from current
holdings or a fixed BTC/ETH/SOL list. Markets under warning or without seven days of completed
15-minute history are ineligible. Eligible markets are first limited by recent quote-volume, then
ranked by a deterministic four-hour/24-hour momentum score with a volatility penalty.

At most three positive-score assets are targeted. The planner creates exits for holdings that are
no longer selected and entries for newly selected assets. It enforces a 90% invested limit, 40%
per-asset cap, minimum order/rebalance thresholds, sell-before-buy ordering, and stable intent IDs.

The hourly scheduler integration is opt-in by Paper portfolio ID and defaults to dry-run. Explicit
execution uses only the deterministic local Paper gateway and SQLite ledger. No private Upbit API,
credential, or live-order adapter is part of this decision.

## Consequences

New assets can enter the portfolio once sufficient forward-collected data exists. Selection remains
reproducible at an as-of time and delisted/warning assets are not bought. Seven days is only an
engineering bootstrap threshold, not evidence of profitability; longer shadow operation and
out-of-sample validation are required before changing the policy or considering live execution.
