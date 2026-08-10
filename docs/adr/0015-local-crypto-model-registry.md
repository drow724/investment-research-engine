# ADR 0015: Local versioned crypto model registry

Status: Accepted

## Decision

Trained artifacts live under `models/crypto/{model_id}` with an atomic pickle, JSON metadata, and
a `LATEST` pointer. The deterministic ID includes the model family, feature list, horizon,
universe mode, and dataset content hash. Metadata includes fold metrics and known limitations.

Pickles are trusted local artifacts only and must never be loaded from an untrusted source. A
future production registry should add signatures, access control, promotion stages, and rollback.
