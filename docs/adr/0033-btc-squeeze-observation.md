# ADR 0033: BTC squeeze data remains an observation-only market overlay

## Status

Accepted.

## Context

The Upbit Paper strategy uses spot candles and cannot infer leveraged futures positioning from
price momentum alone. Binance USD-M futures publishes point-in-time open interest, funding,
perpetual basis, long/short ratios, and taker flow. These inputs can describe short-position fuel,
while completed Upbit BTC candles can describe a limited spot-volume ignition proxy.

Neither a liquidation heatmap nor an account long/short ratio is a ground-truth map of every
position. Price rising while open interest falls can also include position closure unrelated to
forced short liquidation.

## Decision

- Store immutable BTCUSDT derivatives snapshots in a separate SQLite observation database.
- Capture snapshots at minutes 3, 18, 33, and 48 UTC, after the 15-minute spot sync and before the
  Paper rebalance.
- Compute versioned `Fuel`, `Ignition`, and `Active` research states.
- Capture the official Binance BTCUSDT force-order stream continuously and store idempotent
  liquidation events. A short-liquidation total is confirmed only when the complete 15-minute
  window has uninterrupted stream coverage.
- Capture the public Coinbase BTC-USD ticker-batch stream with heartbeats. Compute a Coinbase
  Premium proxy against the contemporaneous Binance BTCUSDT index price only when the Coinbase
  observation is no more than two minutes old.
- Treat the Binance basis series as optional: an empty response or HTTP 418/429 is recorded as an
  explicit `basis_rate` gap, never as a carried-forward or synthetic value. Respect `Retry-After`
  with a basis-only circuit breaker so an active IP restriction is not repeatedly probed. Required
  derivatives metrics and malformed non-empty payloads continue to fail closed.
- Keep Coinbase Premium outside the scoring formula until forward observation establishes value.
- Expose the observations through read-only API and dashboard surfaces.
- Do not use the signal in V2.3 ranking, allocation, turnover, or order creation.

## Consequences

At least one hour of continuous snapshots is required before the first Fuel score can mature.
Fifteen minutes of uninterrupted liquidation-stream coverage is required before zero liquidation
can be distinguished from missing data. The signal can be evaluated without contaminating the
active Paper experiment. A future candidate may consume this overlay only after forward returns and
coverage are pre-registered and validated.
