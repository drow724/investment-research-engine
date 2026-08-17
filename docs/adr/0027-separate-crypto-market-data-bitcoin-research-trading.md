# ADR 0027: Separate Crypto Market Data, Bitcoin Research, and Crypto Trading Contexts

## Status

Accepted

## Context

`investment.bitcoin` began with both BTC research and a Binance price adapter, while
`investment.crypto` grew into a tactical trading engine with Upbit ingestion. Asset-based package
names made market-data ownership ambiguous and invited trading code to reach into research
implementation details.

## Decision

Boundaries follow behavior. `investment.market_data.crypto` owns exchange adapters, symbol/time
normalization, and normalized crypto OHLCV schemas. `investment.bitcoin` specializes in long-term
BTC research, features, structural regimes, and accumulation intelligence. `investment.crypto`
remains the tactical trading context and owns strategy, portfolio, risk, execution, accounting,
backtest, and ML behavior.

The stable cross-context contract is `investment.bitcoin.contracts`. Trading may import only that
published contract, through `investment.crypto.ports.bitcoin_research`; it may not import Bitcoin
features or research internals. Bitcoin research never depends on crypto trading.

```text
                  ┌────────────────────┐
                  │ Crypto Market Data │
                  └─────────┬──────────┘
                            │ normalized observations
              ┌─────────────┴─────────────┐
              ▼                           ▼
    ┌──────────────────┐       ┌──────────────────┐
    │ Bitcoin Research │       │  Crypto Trading  │
    └─────────┬────────┘       └─────────▲────────┘
              └──── published contract ──┘
```

## Allowed dependencies

- Market data → core primitives and external transport libraries.
- Bitcoin research → core and normalized market-data contracts.
- Crypto trading → core, market data, and published Bitcoin research contracts.
- Interface/composition adapters → all application contexts.

## Forbidden dependencies

- Bitcoin research → crypto trading.
- Crypto market data → Bitcoin research or crypto trading.
- Crypto trading → Bitcoin feature/research implementation modules.
- Domain modules → FastAPI, HTTP clients, or exchange adapters.

## Compatibility and migration

The Binance provider, normalizer, and OHLCV schema moved to `investment.market_data.crypto`.
Existing `investment.bitcoin.data.price` modules are forwarding modules so external imports remain
valid; they contain no authoritative implementation. Upbit types still reside under the legacy
crypto tree and are a documented next incremental move. AST architecture tests enforce dependency
direction before further moves.

## Consequences

BTC remains a tradable asset in crypto strategies without making trading part of Bitcoin research.
Research datasets, feature outputs, backtests, and trading ledgers remain separate because their
revision and audit policies differ.
