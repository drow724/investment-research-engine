# ADR 0017: Crypto research lifecycle and manual promotion

Status: Accepted

## Context

Scheduled retraining must not silently deploy a model or strategy. Research changes need a durable,
reproducible audit trail and an explicit human approval boundary.

## Decision

The crypto context owns immutable `Experiment`, `StrategyVersion`, `ModelVersion`,
`DatasetSnapshot`, `TrainingRun`, `BacktestRun`, and `ValidationRun` records. Experiments follow a
validated state machine. A strategy experiment creates a challenger version, compares champion and
challenger on the same checksummed snapshot, and applies a replaceable candidate policy.

Passing evaluation produces `CANDIDATE`, not `ACTIVE`. Only the explicit promote use case, with a
non-empty approver identity, retires the previous champion and activates the candidate. Failure or
rejection leaves the champion unchanged. Completed and failed experiments cannot be rerun, so prior
run records cannot be overwritten accidentally.

JSON persistence is behind `ResearchLifecycleRepository`. It can later be replaced by PostgreSQL
and/or an MLflow-backed artifact adapter without changing the domain. Scheduling remains outside
Python; a future Spring control plane calls the REST use cases.

## Consequences

Local multi-process transactions and concurrent version allocation are not guaranteed yet. Model
version records and training-run persistence exist, while the first executable vertical slice is a
configuration-driven momentum strategy experiment. Production deployment and live orders remain
out of scope.
