# ADR 0008: ETF as a post-2024 overlay

## Context

US spot Bitcoin ETF observations begin in 2024, far later than price, on-chain, and derivatives
history. A good aggregate result over this short sample can easily be unstable.

## Decision

Mark ETF-derived and ETF-dependent composite metadata as `POST_ETF`. Treat price, volume,
on-chain, and derivatives features as the potential `LONG_HISTORY` layer. Do not extrapolate ETF
results to pre-2024 history or assign them equal confidence without sub-period evidence.

## Consequences

ETF experiments have smaller samples and require year/rolling-window reporting. Future models must
keep the institutional overlay distinct from long-history market structure.
