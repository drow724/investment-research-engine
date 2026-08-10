# ADR 0016: ML paper job is dry-run only

Status: Accepted

## Decision

The ML paper job converts ranked positive predictions into equal-weight targets and runs the
independent allocation risk policy. `dryRun` defaults to true and false is rejected. No live or
paper orders are submitted by this job until exchange precision, reservations, reconciliation,
loss controls, and operational promotion gates are implemented.

Scheduling belongs outside the Python process (cron or a future control plane), which calls the
idempotent HTTP workflow after data and universe synchronization.
