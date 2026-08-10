# Crypto Trading Engine foundation

Research changes are governed by ADR 0017. Backtest completion does not mutate an active strategy.
Champion and challenger runs share a checksummed dataset snapshot, a replaceable policy determines
candidate eligibility, and a named human must invoke promotion.

## Boundary

```text
investment.bitcoin
  Long-horizon BTC research, forward returns/risk, interpretation

investment.crypto
  Multi-asset universe, regime, selection, portfolio, risk, backtest, paper execution
```

The two contexts may share asset-independent timestamp, point-in-time, experiment, and statistical
primitives. Bitcoin absorption hypotheses and crypto cross-sectional strategies do not share
portfolio or signal models.

## Dependency flow

```text
FastAPI crypto DTO/router
        ↓
CryptoBacktestService
        ↓
MarketDataProvider port
        ↓
Regime → Strategy → Portfolio Construction → Risk → Backtest

ApprovedOrder → ExchangeGateway port → Paper/ future exchange adapter
```

Strategies receive `StrategyContext` and return `StrategyResult`; neither type exposes an exchange
gateway. Portfolio construction converts signals into weights. The deterministic risk engine may
reject those weights and is the only component that can turn an `OrderIntent` into an
`ApprovedOrder`. Exchange gateways accept only `ApprovedOrder`.

## Portfolio identity

`PortfolioPurpose` distinguishes `CORE_INVESTMENT`, `SYSTEMATIC_TRADING`, and `PAPER_TRADING`.
Objects inside the crypto trading context reject `CORE_INVESTMENT`. Future exchange credentials,
balances, accounting ledgers, and audit records must be scoped by both `portfolio_id` and purpose.

## External adapters

- Spring attaches to the versioned REST endpoints under `/api/v1/crypto/...`; it schedules and
  monitors use cases without orchestrating strategy internals.
- A future MCP adapter belongs beside REST under `investment.interfaces`, calling the same
  application services. It is an AI-to-tool contract, not Spring-to-Python RPC.
- Real exchange implementations belong behind `ExchangeGateway` under crypto infrastructure.
  Strategy modules must never import them.

The first market adapter uses Upbit's public KRW market-list and daily-candle endpoints. Raw candle
payloads are content-addressed JSON; normalized pair files are Parquet. Universe snapshots are
append-only observations: a backtest before the first captured snapshot receives no dynamically
eligible assets rather than today's survivor list.

Paper balances, positions, and fills are persisted in SQLite. Stable intent-derived paper order IDs
and database uniqueness constraints make retries idempotent even after gateway or process restart.
No HTTP execution route is exposed in this phase.

## Before live trading

Required follow-up work includes point-in-time universe membership, delisting/survivorship handling,
exchange-specific precision and minimum-notional rules, persistent double-entry accounting,
idempotent order state machines, partial-fill reconciliation, latency/spread/impact models,
credential isolation, daily loss/drawdown controls, audit events, kill-switch operations, paper/live
parity tests, and an extended shadow-trading period.
