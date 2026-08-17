# ADR 0019: Engine status event reporting

Status: Accepted

## Decision

Engine state and individual job executions are separate immutable models. State and execution
history are persisted locally as atomic JSON, so the engine remains observable when Spring is down.

The application publishes startup, heartbeat, scheduled, started, completed, failed, skipped, and
stopping events through `EngineEventPublisher`. The HTTP adapter sends camelCase JSON to the
configured Spring endpoint and uses `eventId` as its idempotency key. Retry delays and HTTP timeout
are finite and configurable.

Reporting failure never changes a completed research job into a failed job. It marks the engine
`DEGRADED` with `reportingDegraded=true`; a later successful report clears that condition. Error
events contain classified, length-limited messages and never intentionally include credentials or
environment dumps.

## Consequences

The initial adapter retries in-process and has no durable event outbox. Local execution history is
authoritative while Spring is unavailable, but missed events are not replayed automatically yet.
Spring determines `OFFLINE` or `UNKNOWN` from heartbeat age.
