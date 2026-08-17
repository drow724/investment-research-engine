# ADR 0018: Autonomous Python runtime

Status: Accepted

## Decision

The Python Research Engine is an independent long-running execution plane. It owns its schedule and
runs registered application use cases through an internal APScheduler adapter. Spring is not the
trigger for normal research execution; it is the control, monitoring, persistence, and presentation
plane.

Schedules are environment-backed configuration rather than decorators or hard-coded research
logic. The scheduler resolves named application jobs from a registry. The default overlap policy is
`SKIP`, and optional finite timeouts become failed executions with `JOB_TIMEOUT`.

FastAPI and the scheduler share one process lifecycle. Startup initializes runtime state and starts
the scheduler. Shutdown stops accepting work, shuts down scheduling, records `STOPPED`, and emits a
best-effort stopping event. `python -m investment` is the standard autonomous entry point.

## Consequences

This is a lightweight single-process scheduler, not a distributed job system. A deployment must run
one scheduler-bearing instance until leader election or a distributed lock is introduced. Timeout
cannot forcibly terminate arbitrary Python code; jobs should use provider-level network timeouts and
cooperative cancellation where appropriate.
