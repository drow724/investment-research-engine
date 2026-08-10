# ADR 0014: Leakage-safe crypto ML research

Status: Accepted

## Decision

Crypto return models use only completed candles whose `available_at` is no later than the
prediction timestamp. Targets are forward open-to-open returns and are never feature inputs.
Ridge and histogram gradient boosting are compared with calendar walk-forward folds. Every fold
has a purge at least as long as the label horizon, and preprocessing is fitted inside each fold.

The default universe is reconstructed from stored point-in-time snapshots. An explicitly selected
`STATIC_EXPLICIT` mode is allowed for exploration but every artifact and response records
`STATIC_UNIVERSE_SURVIVORSHIP_RISK`.

## Consequences

Training fails when history is too short, the purge is insufficient, or point-in-time membership
is unavailable. This is preferable to silently producing a biased score. Validation IC selects the
model family; held-out test IC remains reporting evidence, not a promotion guarantee.
