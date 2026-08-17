# ADR 0022: Human-approved active ML model

## Status

Accepted

## Decision

Model training updates only the `LATEST` pointer. It never changes the operational `ACTIVE`
pointer. Activation requires an explicit approver and a policy check against the selected model's
walk-forward comparison. Version 1 requires both mean validation IC and held-out mean test IC to be
strictly positive.

Each successful activation writes an immutable audit record containing the model ID, approver,
timestamp, previous active model, policy version, and accepted scores before atomically replacing
the `ACTIVE` pointer. Prediction without an explicit model ID continues to load only `ACTIVE`.

## Consequences

A newly trained or merely latest artifact cannot silently affect Paper decisions. Weak candidates
are rejected consistently, and model changes are attributable. This approval does not prove a
profitable edge and does not authorize live trading. Connecting the approved daily model to the
intraday dynamic Paper selector remains a separate change because their data frequencies and
feature contracts differ.
