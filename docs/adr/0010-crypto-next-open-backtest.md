# ADR 0010: Crypto next-open backtest execution

## Context

A daily strategy cannot observe a completed candle and execute at that candle's opening price.
Doing so would introduce look-ahead bias. Fees and slippage also materially affect small trading
accounts.

## Decision

At each rebalance-day open, strategies receive only candles whose `available_at` is no later than
that open. Approved target weights take effect at that open, and holdings earn open-to-next-open
returns. Turnover is charged configurable fee and slippage rates. Cash and BTC buy-and-hold are
reported as benchmarks.

## Consequences

The reference engine is conservative and deterministic but does not yet model intraday fills,
spread, partial fills, latency, market impact, delisting, or survivorship bias.
