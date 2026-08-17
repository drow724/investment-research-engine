# ADR 0020: Personal intraday research before high-frequency trading

Status: Accepted

## Decision

The first intraday vertical slice uses completed 15-minute candles for signals, an hourly
rebalance cadence (four bars), and a BTC regime derived from UTC-aligned completed four-hour
buckets. This is personal-frequency systematic research, not professional low-latency HFT.

Upbit minute candles are stored separately under `{timeframe}/{pair}.parquet`; daily datasets and
APIs remain backward compatible. A minute candle becomes available only at its closing boundary.
The backtester continues to execute at the next open and uses 35,040 periods per year for 15-minute
volatility and Sharpe annualization.

The autonomous scheduler collects recent 15-minute candles at minutes 01, 16, 31, and 46. The
one-minute delay avoids treating an unfinished boundary as completed data. Historical backfill and
intraday backtests are explicit APIs.

## Consequences

The current slice does not place Paper or live orders. It has no spread/order-book execution model,
partial fills, minimum holding period, turnover budget, or intraday ML model. These controls are
required before increasing trading frequency or connecting an order adapter.
