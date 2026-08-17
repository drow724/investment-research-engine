# Python Runtime → Spring Status Event Contract

The Python engine is the autonomous execution plane. Spring receives these events at the URL in
`INVESTMENT_RUNTIME_EVENT_ENDPOINT`; the recommended endpoint is:

```text
POST /internal/v1/engines/events
Content-Type: application/json
Idempotency-Key: {eventId}
```

Common fields:

```json
{
  "eventId": "evt-unique-uuid",
  "engine": "investment-research-engine",
  "instanceId": "investment-engine-01",
  "eventType": "HEARTBEAT",
  "occurredAt": "2026-08-14T00:00:00+00:00",
  "engineStatus": "IDLE",
  "version": "0.7.0"
}
```

Job events additionally contain `executionId`, `jobName`, `startedAt`, and `finishedAt` when those
values exist. A failure contains:

```json
{
  "error": {
    "category": "DATA_PROVIDER_ERROR",
    "code": "READTIMEOUT",
    "message": "provider request timed out"
  }
}
```

Supported event types:

```text
ENGINE_STARTED
ENGINE_STOPPING
HEARTBEAT
JOB_SCHEDULED
JOB_STARTED
JOB_COMPLETED
JOB_FAILED
JOB_SKIPPED
```

Spring should deduplicate on `eventId`, store events append-only, and calculate `OFFLINE` or
`UNKNOWN` from heartbeat age. Any 2xx response acknowledges delivery. Python retries non-2xx and
transport failures using its finite configured delays. The same event and idempotency key are used
for every retry.

Spring must not use this endpoint to control Python execution. Administrative REST endpoints remain
available on Python, but normal jobs originate from Python's internal scheduler.
