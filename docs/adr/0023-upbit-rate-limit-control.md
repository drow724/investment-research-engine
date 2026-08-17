# ADR 0023: Process-wide Upbit rate-limit control

## Status

Accepted

## Decision

All Upbit public REST clients in one Python process share a thread-safe limiter capped at eight
requests per second, below the documented candle ceiling of ten. Responses are inspected for the
`Remaining-Req` header; `sec=0` defers the next request. HTTP 429 responses receive at most four
retries with bounded exponential backoff and jitter. HTTP 418 temporary blocks are surfaced
immediately and are never retried automatically.

Intraday collection isolates HTTP and response-validation failures by pair. Successful pairs are
persisted even when another pair fails. API callers receive `PARTIAL` with pair-level errors, while
the autonomous runtime marks a partially failed scheduled execution as failed so it remains
observable and can be retried on the next schedule.

## Consequences

Initial seven-day 15-minute backfills take longer but no longer burst four pagination requests per
coin without pacing. Manual API collection and scheduler collection share the same IP-level budget.
Repeated 429 responses remain visible after bounded retries, preventing an infinite retry loop or
escalation into repeated 418 blocks.
