# ADR 0013: Persistent paper accounting

## Context

An in-memory paper exchange cannot validate process restarts, duplicate requests, cash sufficiency,
position cost, or reconciliation behavior.

## Decision

Persist paper portfolios, positions, and executions in SQLite using exact decimal strings. Apply
fills inside an immediate transaction. `intent_id` and `order_id` are unique, so retrying the same
approved intent cannot change balances twice. BUY and SELL fills update cash and average cost, while
execution rows retain fees and realized P&L.

## Consequences

Paper state survives restarts and supports deterministic tests without introducing PostgreSQL or a
queue. This is not yet a full double-entry ledger and does not model reservations, concurrent open
orders, deposits, withdrawals, or exchange reconciliation.
