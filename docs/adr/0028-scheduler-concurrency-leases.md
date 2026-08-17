# ADR 0028: Scheduler Concurrency, Lease, and Idempotency Policies

## Status

Accepted

## Context

APScheduler previously registered every job with `max_instances=2`. Runtime duplicate detection
used only an in-process set keyed by job name. It could neither coordinate multiple processes nor
allow independent portfolios while serializing mutation of one portfolio.

## Decision

Every scheduled job has a `JobExecutionClass` and centralized `JobPolicy`. Read-only jobs may use
two local instances. Refresh/state-mutation/execution jobs use `max_instances=1` and `coalesce=True`.
Mutation jobs have a short misfire grace period, so restart runs only the latest relevant trigger
and never queues all missed trading windows.

State-changing handlers execute under a scoped SQLite lease. A lease records key, owner execution
ID, acquisition time, and expiration. Acquisition is atomic with `BEGIN IMMEDIATE`; only the owner
may release it, and an expired lease can be reclaimed. Current TTLs are 60 minutes for dataset
mutation and 15 minutes for execution. Handler logic depends on the `JobLock` port, allowing a
future PostgreSQL or Redis implementation without changing jobs.

Rebalance uses `portfolio:{portfolio_id}:rebalance`, covering portfolio load through selection,
risk checks, intent creation, Paper execution, and accounting persistence. Different portfolios
therefore remain independent. There is no reconciliation job today; a future reconciliation that
mutates the same order state must share an account execution-state lock.

State-changing executions receive a stable minute-window identity. After acquiring the lease, a
previous successful/running identity produces `SKIPPED_DUPLICATE`. Lease contention produces
`SKIPPED_LOCKED`. Both reuse existing execution persistence and runtime events.

```text
Scheduler trigger
      ↓
central JobPolicy
      ↓
scoped SQLite lease
      ↓
execution identity check
      ↓
application handler
      ↓
existing runtime execution/event
```

## Failure and restart semantics

Normal completion and exceptions release the lease in `finally`. A process crash leaves the lease
until its conservative TTL expires. Locked mutation handlers run synchronously; Python threads
cannot safely cancel a portfolio mutation at a timeout boundary. APScheduler coalesces missed runs,
and execution jobs have a 30-second misfire grace period, so stale windows are not replayed.

## Consequences

Locking prevents overlap but does not replace order/accounting idempotency, which remains enforced
by stable intent IDs and database uniqueness. SQLite is appropriate for the current single-host
deployment; multi-host deployment requires another `JobLock` adapter and shared execution-state
repository.
