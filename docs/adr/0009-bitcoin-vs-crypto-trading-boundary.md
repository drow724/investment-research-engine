# ADR 0009: Bitcoin research versus crypto trading boundary

## Context

The Bitcoin engine researches the user's long-horizon core BTC allocation. The crypto trading
engine ranks multiple assets and may trade BTC tactically. Sharing portfolio identity between them
could let a tactical sell affect protected core holdings.

## Decision

Keep Bitcoin-specific research in `investment.bitcoin` and place cross-sectional trading in the
separate `investment.crypto` bounded context. Trading portfolios accept only `SYSTEMATIC_TRADING`
or `PAPER_TRADING`; `CORE_INVESTMENT` is rejected by portfolio, allocation, order approval, and API
validation boundaries. Reuse only genuinely common time and point-in-time primitives.

## Consequences

BTC can appear in a trading universe without representing the user's core BTC. Any future account
or exchange adapter must map credentials and balances to a specific portfolio purpose before order
approval.
